from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.celery_app import celery_app
from app.config import get_settings
from app.errors import is_transient_error, map_ytdlp_error
from app.jobs import delete_job_record, get_job, list_all_job_ids, update_job_fields
from app.logging_config import log_event, setup_logging
from app.models import JobStatus
from app.network import ensure_host_reachable
from app.storage import get_storage

setup_logging(get_settings().log_level)
logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class JobTimeout(Exception):
    pass


def _impersonate_target() -> str | None:
    try:
        import curl_cffi  # noqa: F401

        return "chrome"
    except ImportError:
        return None


def _is_tiktok(url: str) -> bool:
    host = _safe_domain(url)
    return "tiktok.com" in host


def _set_stage(job_id: str, settings, *, status: JobStatus, progress: int, message: str | None) -> None:
    update_job_fields(
        job_id,
        settings.file_ttl_seconds,
        status=status,
        progress=progress,
        message=message,
        error=None,
    )


def _progress_hook(job_id: str, start_time: float, timeout: int):
    settings = get_settings()

    def hook(d: dict) -> None:
        if time.time() - start_time > timeout:
            raise JobTimeout(f"Job exceeded {timeout}s timeout")

        job = get_job(job_id)
        if job and job.status == JobStatus.CANCELLED:
            raise JobCancelled("Job cancelled by user")

        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            progress = int(downloaded * 100 / total) if total else 0
            progress = max(0, min(progress, 99))
            update_job_fields(
                job_id,
                settings.file_ttl_seconds,
                status=JobStatus.DOWNLOADING,
                progress=progress,
                message="Downloading media…",
                error=None,
            )
        elif d.get("status") == "finished":
            update_job_fields(
                job_id,
                settings.file_ttl_seconds,
                status=JobStatus.PROCESSING,
                progress=99,
                message="Finalizing file…",
                error=None,
            )

    return hook


def _reset_tmp_dir(tmp_dir: Path) -> None:
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)


def _build_ydl_opts(
    *,
    job_id: str,
    url: str,
    tmp_dir: Path,
    start: float,
    settings,
    quality: str = "best",
    audio_format: str = "m4a",
) -> dict:
    from app.formats import audio_postprocessors, is_audio_quality, resolve_ydl_format

    tiktok = _is_tiktok(url)
    socket_timeout = (
        settings.tiktok_socket_timeout if tiktok else settings.download_socket_timeout
    )
    fmt = resolve_ydl_format(quality=quality, url=url, is_tiktok=tiktok)
    audio_only = is_audio_quality(quality)

    ydl_opts: dict = {
        "outtmpl": str(tmp_dir / "%(title).80B [%(id)s].%(ext)s"),
        "format": fmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_progress_hook(job_id, start, settings.job_timeout_seconds)],
        "socket_timeout": socket_timeout,
        "retries": 10 if tiktok else 3,
        "fragment_retries": 10 if tiktok else 3,
        "file_access_retries": 3,
        "max_filesize": settings.max_file_size_mb * 1024 * 1024,
        "match_filter": _duration_filter(settings.max_duration_seconds),
        "extractor_args": {
            "youtube": {"player_client": ["android", "ios", "web"]},
        },
    }

    if audio_only:
        ydl_opts["postprocessors"] = audio_postprocessors(audio_format)
    else:
        ydl_opts["merge_output_format"] = "mp4"

    impersonate = _impersonate_target()
    if impersonate:
        ydl_opts["impersonate"] = impersonate

    cookies = (settings.ytdlp_cookies_file or "").strip()
    if cookies:
        ydl_opts["cookiefile"] = cookies

    proxy = (settings.ytdlp_proxy or "").strip()
    if proxy:
        ydl_opts["proxy"] = proxy

    return ydl_opts


def _run_ytdlp_download(
    *,
    job_id: str,
    url: str,
    tmp_dir: Path,
    opaque: str,
    start: float,
    settings,
    storage,
    quality: str = "best",
    audio_format: str = "m4a",
) -> dict:
    _set_stage(
        job_id,
        settings,
        status=JobStatus.DOWNLOADING,
        progress=0,
        message="Fetching media info…",
    )

    ydl_opts = _build_ydl_opts(
        job_id=job_id,
        url=url,
        tmp_dir=tmp_dir,
        start=start,
        settings=settings,
        quality=quality,
        audio_format=audio_format,
    )

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise DownloadError("No media information returned")

        # Playlist edge: take first entry if present
        if "entries" in info and info["entries"]:
            info = info["entries"][0]

        filename = ydl.prepare_filename(info)
        # After merge / audio extract, extension may change
        path = Path(filename)
        if not path.exists():
            # Audio postprocessors rewrite extension (e.g. .webm → .mp3)
            stem = path.with_suffix("")
            for alt in path.parent.glob(stem.name + ".*"):
                if alt.is_file():
                    path = alt
                    break
        if not path.exists():
            candidates = list(tmp_dir.glob("*"))
            files = [c for c in candidates if c.is_file()]
            if not files:
                raise DownloadError("Downloaded file not found")
            path = max(files, key=lambda p: p.stat().st_size)

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > settings.max_file_size_mb:
            path.unlink(missing_ok=True)
            raise DownloadError(f"File is larger than {settings.max_file_size_mb}MB")

        safe_name = _sanitize_filename(path.name)
        dest = storage.ready_path(opaque, safe_name)
        shutil.move(str(path), str(dest))
        shutil.rmtree(tmp_dir, ignore_errors=True)

        expires_at = time.time() + settings.file_ttl_seconds
        update_job_fields(
            job_id,
            settings.file_ttl_seconds,
            status=JobStatus.DONE,
            progress=100,
            error=None,
            message=None,
            file_path=str(dest),
            file_name=safe_name,
            opaque_token=opaque,
            expires_at=expires_at,
        )
        duration = round(time.time() - start, 2)
        log_event(
            logger,
            "download_done",
            job_id=job_id,
            duration=duration,
            size_mb=round(size_mb, 2),
            quality=quality,
        )
        return {"job_id": job_id, "status": "done"}


@celery_app.task(
    bind=True,
    name="app.tasks.download_media",
    soft_time_limit=get_settings().job_timeout_seconds,
    time_limit=get_settings().job_timeout_seconds + 30,
)
def download_media(self, job_id: str, url: str) -> dict:
    settings = get_settings()
    storage = get_storage(settings)
    start = time.time()
    tmp_dir = storage.tmp_dir(job_id)
    opaque = uuid.uuid4().hex
    max_attempts = max(1, settings.download_retry_attempts)
    backoff = max(0.5, settings.download_retry_backoff_seconds)

    job_meta = get_job(job_id)
    quality = (job_meta.quality if job_meta else "best") or "best"
    audio_format = (job_meta.audio_format if job_meta else "m4a") or "m4a"

    log_event(
        logger,
        "download_started",
        job_id=job_id,
        url_domain=_safe_domain(url),
        quality=quality,
    )

    update_job_fields(
        job_id,
        settings.file_ttl_seconds,
        status=JobStatus.DOWNLOADING,
        progress=0,
        error=None,
        message="Checking network…",
        celery_task_id=self.request.id,
    )

    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        job = get_job(job_id)
        if job and job.status == JobStatus.CANCELLED:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {"job_id": job_id, "status": "cancelled"}

        if attempt > 1:
            _set_stage(
                job_id,
                settings,
                status=JobStatus.RETRYING,
                progress=0,
                message=f"Temporary network issue — retry {attempt}/{max_attempts}…",
            )
            log_event(
                logger,
                "download_retry",
                job_id=job_id,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            time.sleep(backoff * (attempt - 1))
            _reset_tmp_dir(tmp_dir)

        # DNS preflight — avoid burning a long yt-dlp hang when the host is unresolvable.
        try:
            _set_stage(
                job_id,
                settings,
                status=JobStatus.DOWNLOADING if attempt == 1 else JobStatus.RETRYING,
                progress=0,
                message="Waiting for network…" if attempt > 1 else "Checking network…",
            )
            ensure_host_reachable(url)
        except Exception as exc:
            last_exc = exc
            can_retry = attempt < max_attempts
            log_event(
                logger,
                "download_dns_wait",
                job_id=job_id,
                attempt=attempt,
                error=str(exc)[:240],
            )
            if can_retry:
                _set_stage(
                    job_id,
                    settings,
                    status=JobStatus.RETRYING,
                    progress=0,
                    message="Site unreachable (DNS) — waiting to retry…",
                )
                time.sleep(backoff * attempt)
                continue

            shutil.rmtree(tmp_dir, ignore_errors=True)
            msg = map_ytdlp_error(exc)
            update_job_fields(
                job_id,
                settings.file_ttl_seconds,
                status=JobStatus.FAILED,
                error=msg,
                message=None,
                progress=0,
            )
            log_event(logger, "download_failed", job_id=job_id, error=msg, attempts=attempt)
            return {"job_id": job_id, "status": "failed", "error": msg}

        try:
            return _run_ytdlp_download(
                job_id=job_id,
                url=url,
                tmp_dir=tmp_dir,
                opaque=opaque,
                start=start,
                settings=settings,
                storage=storage,
                quality=quality,
                audio_format=audio_format,
            )

        except JobCancelled:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            update_job_fields(
                job_id,
                settings.file_ttl_seconds,
                status=JobStatus.CANCELLED,
                error="Cancelled",
                message=None,
                progress=0,
            )
            log_event(logger, "download_cancelled", job_id=job_id)
            return {"job_id": job_id, "status": "cancelled"}

        except JobTimeout as exc:
            # Time budget exhausted — do not keep retrying past the job timeout.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            msg = map_ytdlp_error(exc)
            update_job_fields(
                job_id,
                settings.file_ttl_seconds,
                status=JobStatus.FAILED,
                error=msg,
                message=None,
                progress=0,
            )
            log_event(logger, "download_timeout", job_id=job_id, error=msg)
            return {"job_id": job_id, "status": "failed"}

        except Exception as exc:
            last_exc = exc
            can_retry = attempt < max_attempts and is_transient_error(exc)
            if can_retry:
                log_event(
                    logger,
                    "download_transient_error",
                    job_id=job_id,
                    attempt=attempt,
                    error=str(exc)[:240],
                )
                continue

            shutil.rmtree(tmp_dir, ignore_errors=True)
            msg = map_ytdlp_error(exc)
            update_job_fields(
                job_id,
                settings.file_ttl_seconds,
                status=JobStatus.FAILED,
                error=msg,
                message=None,
                progress=0,
            )
            log_event(logger, "download_failed", job_id=job_id, error=msg, attempts=attempt)
            return {"job_id": job_id, "status": "failed", "error": msg}

    # Defensive fallback — loop should always return above.
    shutil.rmtree(tmp_dir, ignore_errors=True)
    msg = map_ytdlp_error(last_exc or DownloadError("Download failed"))
    update_job_fields(
        job_id,
        settings.file_ttl_seconds,
        status=JobStatus.FAILED,
        error=msg,
        message=None,
        progress=0,
    )
    return {"job_id": job_id, "status": "failed", "error": msg}


def _duration_filter(max_seconds: int):
    def match(info, *, incomplete):
        duration = info.get("duration")
        if duration is not None and duration > max_seconds:
            return f"Video longer than {max_seconds // 60} minutes is not allowed"
        return None

    return match


def _sanitize_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)
    cleaned = cleaned.strip().replace(" ", "_")
    return cleaned[:180] or "media.bin"


def _safe_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "unknown").lower()
    except Exception:
        return "unknown"


@celery_app.task(name="app.tasks.cleanup_expired")
def cleanup_expired() -> dict:
    settings = get_settings()
    storage = get_storage(settings)
    now = time.time()
    deleted_files = 0
    deleted_jobs = 0

    for job_id in list(list_all_job_ids()):
        job = get_job(job_id)
        if not job:
            delete_job_record(job_id)
            deleted_jobs += 1
            continue

        expired = False
        if job.expires_at and job.expires_at < now:
            expired = True
        elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED) and (now - job.created_at) > settings.file_ttl_seconds:
            expired = True
        elif job.status == JobStatus.QUEUED and (now - job.created_at) > settings.job_timeout_seconds + 300:
            expired = True

        if expired:
            if job.file_path:
                storage.delete_path(job.file_path)
                deleted_files += 1
            # Also clean tmp if present
            tmp = storage.tmp / job_id
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
                deleted_files += 1
            delete_job_record(job_id)
            deleted_jobs += 1

    # Orphan tmp dirs
    if storage.tmp.exists():
        for child in storage.tmp.iterdir():
            if child.is_dir():
                job = get_job(child.name)
                if not job:
                    shutil.rmtree(child, ignore_errors=True)
                    deleted_files += 1

    # Orphan ready dirs older than TTL*2 with no job reference
    if storage.ready.exists():
        for child in storage.ready.iterdir():
            if not child.is_dir():
                continue
            age = now - child.stat().st_mtime
            if age > settings.file_ttl_seconds * 2:
                shutil.rmtree(child, ignore_errors=True)
                deleted_files += 1

    log_event(logger, "cleanup_ran", deleted_files=deleted_files, deleted_jobs=deleted_jobs)
    return {"deleted_files": deleted_files, "deleted_jobs": deleted_jobs}
