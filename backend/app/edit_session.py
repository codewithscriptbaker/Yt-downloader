"""Shared prepare / progress / finalize helpers for media edit API routes."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, status

from app.config import Settings
from app.jobs import get_job, update_job_fields
from app.media_edit import MediaEditCancelled, MediaEditError, is_trimmable_file, is_video_file
from app.models import JobRecord, JobStatus, TrimJobResponse
from app.redis_client import get_redis
from app.signing import build_download_path, create_signed_download_token
from app.storage import get_storage

logger = logging.getLogger(__name__)

EDIT_LOCK_SECONDS = 600
EDIT_CANCEL_KEY = "job:edit:cancel:{job_id}"
EDIT_LOCK_KEY = "job:edit:{job_id}"
# Back-compat with older trim lock key
TRIM_LOCK_KEY = "job:trim:{job_id}"


@dataclass
class EditContext:
    job: JobRecord
    primary: Path
    original_path: Path
    original_name: str
    original_size_mb: float
    previous_path: Path | None
    previous_name: str | None
    previous_size_mb: float | None
    lock_keys: list[str]
    started: float
    source: Path  # usually original for destructive edits from source timeline


def cancel_key(job_id: str) -> str:
    return EDIT_CANCEL_KEY.format(job_id=job_id)


def clear_cancel_flag(job_id: str) -> None:
    try:
        get_redis().delete(cancel_key(job_id))
    except Exception:
        pass


def set_cancel_flag(job_id: str) -> None:
    try:
        get_redis().set(cancel_key(job_id), "1", ex=EDIT_LOCK_SECONDS)
    except Exception:
        pass


def is_cancel_requested(job_id: str) -> bool:
    try:
        return bool(get_redis().get(cancel_key(job_id)))
    except Exception:
        return False


def acquire_edit_lock(job_id: str) -> list[str]:
    r = get_redis()
    keys = [EDIT_LOCK_KEY.format(job_id=job_id), TRIM_LOCK_KEY.format(job_id=job_id)]
    # Prefer new key; also take legacy trim key so concurrent trim/edit cannot race
    if not r.set(keys[0], "1", nx=True, ex=EDIT_LOCK_SECONDS):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An edit is already in progress for this file.",
        )
    r.set(keys[1], "1", nx=True, ex=EDIT_LOCK_SECONDS)
    clear_cancel_flag(job_id)
    return keys


def release_edit_lock(lock_keys: list[str], job_id: str) -> None:
    r = get_redis()
    try:
        if lock_keys:
            r.delete(*lock_keys)
    except Exception:
        pass
    clear_cancel_flag(job_id)


def require_ready_job(job_id: str, ip: str, settings: Settings) -> JobRecord:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.ip != ip:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if job.status != JobStatus.DONE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is not ready")
    if not job.opaque_token or not job.file_name or not job.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing")
    if job.expires_at and job.expires_at < time.time():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="File expired")

    path = Path(job.file_path)
    storage = get_storage(settings)
    try:
        path.resolve().relative_to(storage.ready.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid file path")
    if not path.is_file() and not (
        job.original_file_path and Path(job.original_file_path).is_file()
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    check = path if path.is_file() else Path(job.original_file_path or "")
    if not is_trimmable_file(check):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Edit is only available for audio and video downloads.",
        )
    return job


def ensure_original(job: JobRecord, primary: Path) -> tuple[Path, str, float]:
    original_path = Path(job.original_file_path) if job.original_file_path else None
    original_name = job.original_file_name
    original_size_mb = job.original_file_size_mb

    if original_path is None or not original_path.is_file():
        if not primary.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        original_name = f"{primary.stem}_original{primary.suffix}"
        original_path = primary.with_name(original_name)
        try:
            shutil.copy2(primary, original_path)
            original_size_mb = round(original_path.stat().st_size / (1024 * 1024), 2)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not keep a copy of the original file.",
            ) from exc
    assert original_name is not None and original_size_mb is not None
    return original_path, original_name, float(original_size_mb)


def snapshot_previous(primary: Path) -> tuple[Path | None, str | None, float | None]:
    """Copy current primary → *_previous so undo-last works."""
    if not primary.is_file():
        return None, None, None
    previous_name = f"{primary.stem}_previous{primary.suffix}"
    # Avoid colliding with *_original naming if stem already ends oddly
    if primary.stem.endswith("_original"):
        previous_name = f"{primary.stem}_previous{primary.suffix}"
    previous_path = primary.with_name(previous_name)
    try:
        shutil.copy2(primary, previous_path)
        size_mb = round(previous_path.stat().st_size / (1024 * 1024), 2)
        return previous_path, previous_name, size_mb
    except OSError:
        logger.warning("previous_snapshot_failed path=%s", primary, exc_info=True)
        return None, None, None


def begin_edit(
    job_id: str,
    ip: str,
    settings: Settings,
    *,
    message: str,
    source_from_original: bool = True,
    snapshot: bool = True,
) -> EditContext:
    job = require_ready_job(job_id, ip, settings)
    primary = Path(job.file_path)  # type: ignore[arg-type]
    lock_keys = acquire_edit_lock(job_id)
    try:
        original_path, original_name, original_size_mb = ensure_original(job, primary)
        prev_path, prev_name, prev_size = (None, None, None)
        if snapshot and primary.is_file():
            prev_path, prev_name, prev_size = snapshot_previous(primary)

        source = original_path if source_from_original else primary
        if not source.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        update_job_fields(
            job_id,
            settings.file_ttl_seconds,
            status=JobStatus.PROCESSING,
            progress=1,
            message=message,
            error=None,
            original_file_path=str(original_path),
            original_file_name=original_name,
            original_file_size_mb=original_size_mb,
            previous_file_path=str(prev_path) if prev_path else job.previous_file_path,
            previous_file_name=prev_name if prev_path else job.previous_file_name,
            previous_file_size_mb=prev_size if prev_path else job.previous_file_size_mb,
            has_previous_edit=bool(prev_path or job.has_previous_edit),
            has_trim=False,
        )
        return EditContext(
            job=job,
            primary=primary,
            original_path=original_path,
            original_name=original_name,
            original_size_mb=original_size_mb,
            previous_path=prev_path,
            previous_name=prev_name,
            previous_size_mb=prev_size,
            lock_keys=lock_keys,
            started=time.monotonic(),
            source=source,
        )
    except Exception:
        release_edit_lock(lock_keys, job_id)
        raise


def make_progress_cb(
    ctx: EditContext,
    settings: Settings,
    *,
    label: str,
) -> Callable[[float], None]:
    def _progress(frac: float) -> None:
        pct = max(1, min(99, int(frac * 100)))
        elapsed = max(0.1, time.monotonic() - ctx.started)
        eta_s = int((elapsed / max(frac, 0.02)) * (1.0 - frac)) if frac < 0.99 else 0
        if eta_s >= 60:
            eta_txt = f"~{eta_s // 60}m {eta_s % 60}s left"
        elif eta_s > 0:
            eta_txt = f"~{eta_s}s left"
        else:
            eta_txt = "finishing…"
        update_job_fields(
            ctx.job.job_id,
            settings.file_ttl_seconds,
            status=JobStatus.PROCESSING,
            progress=pct,
            message=f"{label}… {pct}% · {eta_txt}",
            error=None,
            original_file_path=str(ctx.original_path),
            original_file_name=ctx.original_name,
            original_file_size_mb=ctx.original_size_mb,
            previous_file_path=str(ctx.previous_path) if ctx.previous_path else None,
            previous_file_name=ctx.previous_name,
            previous_file_size_mb=ctx.previous_size_mb,
            has_previous_edit=bool(ctx.previous_path),
            has_trim=False,
        )

    return _progress


def fail_edit(ctx: EditContext, settings: Settings, *, keep_has_trim: bool) -> None:
    update_job_fields(
        ctx.job.job_id,
        settings.file_ttl_seconds,
        status=JobStatus.DONE,
        progress=100,
        message=None,
        error=None,
        original_file_path=str(ctx.original_path) if ctx.original_path.is_file() else None,
        original_file_name=ctx.original_name if ctx.original_path.is_file() else None,
        original_file_size_mb=ctx.original_size_mb if ctx.original_path.is_file() else None,
        previous_file_path=str(ctx.previous_path)
        if ctx.previous_path and ctx.previous_path.is_file()
        else ctx.job.previous_file_path,
        previous_file_name=ctx.previous_name
        if ctx.previous_path and ctx.previous_path.is_file()
        else ctx.job.previous_file_name,
        previous_file_size_mb=ctx.previous_size_mb
        if ctx.previous_path and ctx.previous_path.is_file()
        else ctx.job.previous_file_size_mb,
        has_previous_edit=bool(
            (ctx.previous_path and ctx.previous_path.is_file()) or ctx.job.has_previous_edit
        ),
        has_trim=keep_has_trim,
    )


def finish_edit(
    ctx: EditContext,
    settings: Settings,
    *,
    duration_seconds: float,
    operation: str,
) -> TrimJobResponse:
    size_mb = round(ctx.primary.stat().st_size / (1024 * 1024), 2)
    expires_at = time.time() + settings.file_ttl_seconds
    has_prev = bool(ctx.previous_path and ctx.previous_path.is_file())
    update_job_fields(
        ctx.job.job_id,
        settings.file_ttl_seconds,
        status=JobStatus.DONE,
        progress=100,
        message=None,
        error=None,
        file_size_mb=size_mb,
        expires_at=expires_at,
        original_file_path=str(ctx.original_path),
        original_file_name=ctx.original_name,
        original_file_size_mb=ctx.original_size_mb,
        previous_file_path=str(ctx.previous_path) if has_prev else None,
        previous_file_name=ctx.previous_name if has_prev else None,
        previous_file_size_mb=ctx.previous_size_mb if has_prev else None,
        has_previous_edit=has_prev,
        has_trim=True,
    )
    token, ttl = create_signed_download_token(
        ctx.job.job_id, ctx.job.opaque_token, ctx.job.file_name, settings
    )
    otoken, _ = create_signed_download_token(
        ctx.job.job_id, ctx.job.opaque_token, ctx.original_name, settings
    )
    kind = "video" if is_video_file(ctx.primary) else "audio"
    return TrimJobResponse(
        job_id=ctx.job.job_id,
        download_url=build_download_path(ctx.job.job_id, token, ctx.job.file_name),
        original_download_url=build_download_path(ctx.job.job_id, otoken, ctx.original_name),
        expires_in=ttl,
        expires_at=expires_at,
        file_size_mb=size_mb,
        original_file_size_mb=ctx.original_size_mb,
        duration_seconds=round(duration_seconds, 3),
        kind=kind,
        has_trim=True,
        has_previous_edit=has_prev,
        operation=operation,
    )


def map_edit_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MediaEditCancelled):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, MediaEditError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    logger.exception("edit_failed")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Edit failed. Please try again.",
    )
