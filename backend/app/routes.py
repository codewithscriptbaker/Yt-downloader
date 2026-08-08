from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse

from app.captcha import verify_captcha
from app.config import Settings, get_settings
from app.errors import hint_for_error
from app.jobs import (
    count_active_jobs_for_ip,
    create_job,
    get_hourly_submissions,
    get_job,
    increment_hourly_submissions,
    save_job,
    update_job_fields,
)
from app.logging_config import log_event
from app.models import (
    ACTIVE_STATUSES,
    CreateBatchJobRequest,
    CreateBatchJobResponse,
    CreateJobRequest,
    CreateJobResponse,
    DownloadResponse,
    HealthResponse,
    JobStatus,
    JobStatusResponse,
    PreviewRequest,
    PreviewResponse,
)
from app.preview import fetch_preview
from app.redis_client import get_redis, ping_redis
from app.signing import (
    build_download_path,
    create_signed_download_token,
    verify_signed_download_token,
)
from app.storage import disk_usage_mb, get_storage, is_disk_full
from app.validation import validate_media_url
from yt_dlp.utils import DownloadError

logger = logging.getLogger(__name__)
router = APIRouter()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@router.get("/api/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    redis_ok = ping_redis()
    usage = disk_usage_mb(settings)
    return HealthResponse(
        status="ok" if redis_ok else "degraded",
        redis="up" if redis_ok else "down",
        disk_usage_mb=round(usage, 2),
        disk_limit_mb=settings.disk_usage_limit_mb,
    )


@router.post("/api/jobs", response_model=CreateJobResponse, status_code=status.HTTP_201_CREATED)
async def create_download_job(
    body: CreateJobRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CreateJobResponse:
    ip = client_ip(request)
    await verify_captcha(body.captcha_token, ip, settings)

    if is_disk_full(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is full. Please try again later.",
        )

    hourly = get_hourly_submissions(ip)
    if hourly >= settings.max_jobs_per_ip_per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: max {settings.max_jobs_per_ip_per_hour} jobs per hour.",
        )

    active = count_active_jobs_for_ip(ip)
    if active >= settings.max_jobs_per_ip:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many active jobs. Max {settings.max_jobs_per_ip} at a time.",
        )

    url, domain = validate_media_url(body.url, settings)
    # Audio format only applies when quality is audio
    audio_format = body.audio_format if body.quality == "audio" else "m4a"
    record = create_job(
        url,
        domain,
        ip,
        settings.file_ttl_seconds,
        quality=body.quality,
        audio_format=audio_format,
    )
    increment_hourly_submissions(ip, settings.max_jobs_per_ip_per_hour)

    # Lazy import to avoid circular issues at module load in API-only contexts
    from app.tasks import download_media

    async_result = download_media.delay(record.job_id, url)
    record.celery_task_id = async_result.id
    save_job(record, settings.file_ttl_seconds)

    log_event(
        logger,
        "job_created",
        job_id=record.job_id,
        url_domain=domain,
        ip=ip,
        quality=body.quality,
    )
    return CreateJobResponse(job_id=record.job_id)


@router.post(
    "/api/jobs/batch",
    response_model=CreateBatchJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_download_jobs_batch(
    body: CreateBatchJobRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CreateBatchJobResponse:
    """Enqueue one job per URL (playlist multi-select). Same quality for all."""
    ip = client_ip(request)
    await verify_captcha(body.captcha_token, ip, settings)

    if is_disk_full(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage is full. Please try again later.",
        )

    urls = body.urls
    if len(urls) > settings.max_playlist_select:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Select at most {settings.max_playlist_select} videos at a time.",
        )

    hourly = get_hourly_submissions(ip)
    if hourly + len(urls) > settings.max_jobs_per_ip_per_hour:
        remaining = max(0, settings.max_jobs_per_ip_per_hour - hourly)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {remaining} job(s) left this hour "
                f"(max {settings.max_jobs_per_ip_per_hour}/hour)."
            ),
        )

    active = count_active_jobs_for_ip(ip)
    # Allow playlist batches up to max_playlist_select concurrent for this IP
    active_cap = max(settings.max_jobs_per_ip, settings.max_playlist_select)
    if active + len(urls) > active_cap:
        free = max(0, active_cap - active)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many active jobs. You can start {free} more "
                f"(max {active_cap} at a time)."
            ),
        )

    validated: list[tuple[str, str]] = []
    for raw in urls:
        validated.append(validate_media_url(raw, settings))

    audio_format = body.audio_format if body.quality == "audio" else "m4a"
    from app.tasks import download_media

    job_ids: list[str] = []
    for url, domain in validated:
        record = create_job(
            url,
            domain,
            ip,
            settings.file_ttl_seconds,
            quality=body.quality,
            audio_format=audio_format,
        )
        increment_hourly_submissions(ip, settings.max_jobs_per_ip_per_hour)
        async_result = download_media.delay(record.job_id, url)
        record.celery_task_id = async_result.id
        save_job(record, settings.file_ttl_seconds)
        job_ids.append(record.job_id)
        log_event(
            logger,
            "job_created",
            job_id=record.job_id,
            url_domain=domain,
            ip=ip,
            quality=body.quality,
            batch=True,
        )

    log_event(logger, "job_batch_created", ip=ip, count=len(job_ids), quality=body.quality)
    return CreateBatchJobResponse(job_ids=job_ids)


@router.post("/api/preview", response_model=PreviewResponse)
async def preview_media(
    body: PreviewRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> PreviewResponse:
    """Fetch title/thumbnail/duration without enqueueing a download."""
    ip = client_ip(request)
    await verify_captcha(body.captcha_token, ip, settings)

    url, domain = validate_media_url(body.url, settings)

    # Light rate limit for metadata probes (separate from job quota)
    r = get_redis()
    preview_key = f"ip:preview:{ip}"
    count = int(r.incr(preview_key))
    if count == 1:
        r.expire(preview_key, 3600)
    if count > 40:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many previews. Try again later.",
        )

    try:
        result = await asyncio.to_thread(fetch_preview, url, settings)
    except DownloadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("preview_failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not fetch media info. Try again.",
        ) from exc

    log_event(logger, "preview_ok", url_domain=domain, ip=ip)
    return result


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JobStatusResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    download_url = None
    if job.status == JobStatus.DONE and job.opaque_token and job.file_name:
        token, _ = create_signed_download_token(
            job.job_id, job.opaque_token, job.file_name, settings
        )
        download_url = build_download_path(job.job_id, token, job.file_name)

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        error=job.error,
        error_hint=hint_for_error(job.error) if job.error else None,
        message=job.message,
        download_url=download_url,
        expires_at=job.expires_at,
        quality=job.quality,
        audio_format=job.audio_format,
    )


@router.get("/api/jobs/{job_id}/download", response_model=DownloadResponse)
def get_download_link(
    job_id: str,
    settings: Settings = Depends(get_settings),
) -> DownloadResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status != JobStatus.DONE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is not ready")
    if not job.opaque_token or not job.file_name or not job.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing")

    if job.expires_at and job.expires_at < __import__("time").time():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="File expired")

    token, ttl = create_signed_download_token(
        job.job_id, job.opaque_token, job.file_name, settings
    )
    return DownloadResponse(
        download_url=build_download_path(job.job_id, token, job.file_name),
        expires_in=ttl,
    )


@router.get("/api/jobs/{job_id}/file")
def stream_file(
    job_id: str,
    token: str,
    name: str,
    settings: Settings = Depends(get_settings),
):
    job = get_job(job_id)
    if not job or job.status != JobStatus.DONE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not job.opaque_token or not job.file_path or not job.file_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if name != job.file_name:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid file name")

    verify_signed_download_token(job_id, job.opaque_token, job.file_name, token, settings)

    path = Path(job.file_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Prevent path traversal — file must live under storage ready dir
    storage = get_storage(settings)
    try:
        path.resolve().relative_to(storage.ready.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid file path")

    return FileResponse(
        path,
        filename=job.file_name,
        media_type="application/octet-stream",
    )


@router.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_job(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
):
    ip = client_ip(request)
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.ip != ip:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    if job.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is not active")

    if job.celery_task_id:
        from app.celery_app import celery_app

        celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")

    update_job_fields(
        job_id,
        settings.file_ttl_seconds,
        status=JobStatus.CANCELLED,
        error="Cancelled",
        progress=0,
    )
    log_event(logger, "job_cancelled", job_id=job_id, ip=ip)
    return None


@router.websocket("/ws/jobs/{job_id}")
async def job_status_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()
    job = get_job(job_id)
    if not job:
        await websocket.send_json({"error": "Job not found"})
        await websocket.close(code=4404)
        return

    await websocket.send_json(_ws_payload(job))
    if job.status not in ACTIVE_STATUSES:
        await websocket.close()
        return

    r = get_redis()
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    channel = f"job:status:{job_id}"
    pubsub.subscribe(channel)

    try:
        while True:
            message = await asyncio.to_thread(
                pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message.get("type") == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)
                if data.get("status") not in ("queued", "downloading", "retrying", "processing"):
                    break
            else:
                current = get_job(job_id)
                if current and current.status not in ACTIVE_STATUSES:
                    await websocket.send_json(_ws_payload(current))
                    break
                # Keepalive: send a lightweight ping frame if supported
                try:
                    await websocket.send_json({"type": "ping"})
                except (WebSocketDisconnect, RuntimeError):
                    break
    except WebSocketDisconnect:
        pass
    finally:
        try:
            pubsub.unsubscribe(channel)
            pubsub.close()
        except Exception:
            pass


def _ws_payload(job) -> dict:
    from app.errors import hint_for_error

    return {
        "job_id": job.job_id,
        "status": job.status.value if hasattr(job.status, "value") else job.status,
        "progress": job.progress,
        "error": job.error,
        "error_hint": hint_for_error(job.error) if job.error else None,
        "message": getattr(job, "message", None),
        "expires_at": job.expires_at,
        "quality": getattr(job, "quality", None),
        "audio_format": getattr(job, "audio_format", None),
    }
