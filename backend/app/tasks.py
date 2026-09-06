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
from app.errors import is_permanent_error, is_retryable_with_fallback, map_ytdlp_error
from app.jobs import delete_job_record, get_job, list_all_job_ids, update_job_fields
from app.logging_config import log_event, setup_logging
from app.models import JobStatus
from app.network import ensure_host_reachable
from app.storage import get_storage
from app.ytdlp_support import (
    DownloadStrategy,
    apply_common_opts,
    build_download_strategies,
    effective_quality,
    is_facebook_url,
    resolve_impersonate_target,
)

setup_logging(get_settings().log_level)
logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class JobTimeout(Exception):
    pass


def _is_tiktok(url: str) -> bool:
    host = _safe_domain(url)
    return "tiktok.com" in host


def _is_facebook(url: str) -> bool:
    return is_facebook_url(url)


def _socket_timeout_for(url: str, settings) -> int:
    if _is_tiktok(url):
        return settings.tiktok_socket_timeout
    if _is_facebook(url):
        return getattr(settings, "facebook_socket_timeout", 60)
    return settings.download_socket_timeout


def _needs_extra_retries(url: str) -> bool:
    return _is_tiktok(url) or _is_facebook(url)


def _set_stage(job_id: str, settings, *, status: JobStatus, progress: int, message: str | None) -> None:
    """
    Update job progress for clients.

    Internal retries stay on DOWNLOADING with a generic message so the UI never
    surfaces retry bookkeeping (attempt counts, DNS waits, strategy names).
    """
    public_status = status
    public_message = message
    if status == JobStatus.RETRYING:
        public_status = JobStatus.DOWNLOADING
        public_message = "Downloading…"
    update_job_fields(
        job_id,
        settings.file_ttl_seconds,
        status=public_status,
        progress=progress,
        message=public_message,
        error=None,
    )


def _retry_sleep(backoff: float, attempt: int) -> None:
    """Exponential backoff: backoff * 2^(attempt-1), capped."""
    delay = min(60.0, backoff * (2 ** max(0, attempt - 1)))
    time.sleep(delay)


def _progress_hook(
    job_id: str,
    start_time: float,
    timeout: int,
    *,
    expect_merge: bool = True,
):
    """
    Smooth yt-dlp progress for single- and multi-stream downloads.

    YouTube/DASH often downloads video then audio as separate files. Each file
    reports 0–100% on its own, which made the UI jump to ~80% then snap back.
    We map files into phases and never decrease progress within one attempt.
    """
    settings = get_settings()
    # Mutable attempt state (one hook instance per ydl run)
    state: dict = {
        "last": 0,
        "files_done": 0,
        "current_file": None,
        # video+audio merge → 2 files; audio-only / progressive → 1
        "expected_files": 2 if expect_merge else 1,
        "last_publish": 0.0,
    }
    download_span = 92  # leave 92–99 for merge / finalize

    def _file_fraction(d: dict) -> float:
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if total and total > 0:
            return max(0.0, min(1.0, float(downloaded) / float(total)))
        # HLS / DASH fragments when byte totals are missing
        frag_i = d.get("fragment_index")
        frag_c = d.get("fragment_count")
        if frag_c and frag_i is not None:
            try:
                return max(0.0, min(1.0, float(frag_i) / float(frag_c)))
            except (TypeError, ValueError, ZeroDivisionError):
                return 0.0
        return 0.0

    def _map_progress(file_frac: float) -> int:
        n = max(1, int(state["expected_files"]))
        done = min(int(state["files_done"]), n - 1)
        per = download_span / n
        raw = done * per + file_frac * per
        return int(max(0, min(download_span - 1, raw)))

    def _publish(progress: int, *, status: JobStatus, message: str, force: bool = False) -> None:
        progress = max(0, min(99, progress))
        # Monotonic within this download attempt
        if progress < state["last"] and not force:
            progress = state["last"]
        now = time.time()
        # Throttle Redis/WS spam unless we moved ≥1% or it's a stage change
        if (
            not force
            and progress == state["last"]
            and (now - state["last_publish"]) < 0.45
        ):
            return
        if (
            not force
            and progress > state["last"]
            and progress - state["last"] < 1
            and (now - state["last_publish"]) < 0.35
        ):
            return
        state["last"] = progress
        state["last_publish"] = now
        update_job_fields(
            job_id,
            settings.file_ttl_seconds,
            status=status,
            progress=progress,
            message=message,
            error=None,
        )

    def hook(d: dict) -> None:
        if time.time() - start_time > timeout:
            raise JobTimeout(f"Job exceeded {timeout}s timeout")

        job = get_job(job_id)
        if job and job.status == JobStatus.CANCELLED:
            raise JobCancelled("Job cancelled by user")

        status = d.get("status")
        if status == "downloading":
            filename = d.get("filename") or ""
            # New stream started (e.g. audio after video) without relying only on "finished"
            if (
                filename
                and state["current_file"]
                and filename != state["current_file"]
            ):
                # Adaptive: progressive guess was wrong — we actually have 2+ parts
                if state["expected_files"] < 2:
                    state["expected_files"] = 2
                state["files_done"] = min(
                    state["files_done"] + 1,
                    max(0, state["expected_files"] - 1),
                )
            if filename:
                state["current_file"] = filename

            frac = _file_fraction(d)
            progress = _map_progress(frac)
            n = max(1, int(state["expected_files"]))
            if n > 1 and state["files_done"] == 0:
                msg = "Downloading video…"
            elif n > 1 and state["files_done"] >= 1:
                msg = "Downloading audio…"
            else:
                msg = "Downloading media…"
            _publish(progress, status=JobStatus.DOWNLOADING, message=msg)

        elif status == "finished":
            # One part done (video or audio) — do NOT jump to 99% yet if more parts remain
            state["files_done"] = min(
                state["files_done"] + 1,
                int(state["expected_files"]),
            )
            state["current_file"] = None
            if state["files_done"] >= state["expected_files"]:
                _publish(
                    96,
                    status=JobStatus.PROCESSING,
                    message="Finalizing file…",
                    force=True,
                )
            else:
                # Land at the start of the next phase (smooth handoff)
                progress = _map_progress(0.0)
                _publish(
                    max(progress, state["last"]),
                    status=JobStatus.DOWNLOADING,
                    message="Downloading audio…" if expect_merge else "Downloading media…",
                    force=True,
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
    strategy: DownloadStrategy | None = None,
    impersonate_target=None,
) -> dict:
    from app.formats import audio_postprocessors, is_audio_quality, resolve_ydl_format

    strategy = strategy or DownloadStrategy(
        name="standard",
        use_impersonate=False,
        player_clients=("android", "ios", "web"),
        soft_format=False,
        message="Fetching media…",
    )
    q = effective_quality(quality, strategy)

    tiktok = _is_tiktok(url)
    facebook = _is_facebook(url)
    socket_timeout = _socket_timeout_for(url, settings)
    extra_retries = _needs_extra_retries(url)
    fmt = resolve_ydl_format(
        quality=q, url=url, is_tiktok=tiktok, is_facebook=facebook
    )
    audio_only = is_audio_quality(q)
    # Separate A/V streams need merge — except progressive-first sites often get one file
    expect_merge = (not audio_only) and (not tiktok) and (not facebook)

    ydl_opts: dict = {
        "outtmpl": str(tmp_dir / "%(title).80B [%(id)s].%(ext)s"),
        "format": fmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [
            _progress_hook(
                job_id,
                start,
                settings.job_timeout_seconds,
                expect_merge=expect_merge,
            )
        ],
        "socket_timeout": socket_timeout,
        "retries": 10 if extra_retries else 3,
        "fragment_retries": 10 if extra_retries else 3,
        "file_access_retries": 3,
        "max_filesize": settings.max_file_size_mb * 1024 * 1024,
        "match_filter": _duration_filter(settings.max_duration_seconds),
    }

    if audio_only:
        ydl_opts["postprocessors"] = audio_postprocessors(audio_format)
    else:
        ydl_opts["merge_output_format"] = "mp4"

    apply_common_opts(
        ydl_opts,
        settings=settings,
        strategy=strategy,
        impersonate_target=impersonate_target,
        url=url,
    )
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
    strategy: DownloadStrategy | None = None,
    impersonate_target=None,
) -> dict:
    stage_msg = strategy.message if strategy else "Fetching media info…"
    _set_stage(
        job_id,
        settings,
        status=JobStatus.DOWNLOADING,
        progress=0,
        message=stage_msg,
    )

    ydl_opts = _build_ydl_opts(
        job_id=job_id,
        url=url,
        tmp_dir=tmp_dir,
        start=start,
        settings=settings,
        quality=quality,
        audio_format=audio_format,
        strategy=strategy,
        impersonate_target=impersonate_target,
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
            file_size_mb=round(size_mb, 2),
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
            strategy=strategy.name if strategy else "standard",
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
    backoff = max(0.5, settings.download_retry_backoff_seconds)

    job_meta = get_job(job_id)
    quality = (job_meta.quality if job_meta else "best") or "best"
    audio_format = (job_meta.audio_format if job_meta else "m4a") or "m4a"

    impersonate_target = resolve_impersonate_target()
    strategies = build_download_strategies(
        quality=quality,
        impersonate_available=impersonate_target is not None,
        url=url,
    )
    if _is_facebook(url) and impersonate_target is None:
        log_event(
            logger,
            "facebook_without_impersonate",
            job_id=job_id,
            hint="Install curl_cffi for more reliable Facebook downloads",
        )
    # Strategy ladder + configured retry budget
    max_attempts = max(len(strategies), max(1, settings.download_retry_attempts))
    network_retries = max(1, getattr(settings, "download_network_retries", 5))

    log_event(
        logger,
        "download_started",
        job_id=job_id,
        url_domain=_safe_domain(url),
        quality=quality,
        strategies=[s.name for s in strategies],
        max_attempts=max_attempts,
        network_retries=network_retries,
    )

    update_job_fields(
        job_id,
        settings.file_ttl_seconds,
        status=JobStatus.DOWNLOADING,
        progress=0,
        error=None,
        message="Downloading…",
        celery_task_id=self.request.id,
    )

    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        job = get_job(job_id)
        if job and job.status == JobStatus.CANCELLED:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {"job_id": job_id, "status": "cancelled"}

        strategy = strategies[min(attempt - 1, len(strategies) - 1)]
        has_next = attempt < max_attempts

        if attempt > 1:
            log_event(
                logger,
                "download_retry",
                job_id=job_id,
                attempt=attempt,
                max_attempts=max_attempts,
                strategy=strategy.name,
            )
            _retry_sleep(backoff, attempt - 1)
            _reset_tmp_dir(tmp_dir)

        # Per-strategy silent network/DNS retries (not shown to clients)
        for net_try in range(1, network_retries + 1):
            job = get_job(job_id)
            if job and job.status == JobStatus.CANCELLED:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return {"job_id": job_id, "status": "cancelled"}

            # Reset progress only at the start of a strategy attempt — not on every
            # silent network retry (that caused the bar to jump back mid-download).
            if net_try == 1:
                _set_stage(
                    job_id,
                    settings,
                    status=JobStatus.DOWNLOADING,
                    progress=0,
                    message="Downloading…",
                )
            else:
                update_job_fields(
                    job_id,
                    settings.file_ttl_seconds,
                    status=JobStatus.DOWNLOADING,
                    message="Downloading…",
                    error=None,
                )

            try:
                ensure_host_reachable(url)
            except Exception as exc:
                last_exc = exc
                log_event(
                    logger,
                    "download_dns_wait",
                    job_id=job_id,
                    attempt=attempt,
                    net_try=net_try,
                    network_retries=network_retries,
                    error=str(exc)[:240],
                )
                if net_try < network_retries:
                    _retry_sleep(backoff, net_try)
                    continue
                if has_next:
                    break  # advance strategy ladder
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
                log_event(
                    logger,
                    "download_failed",
                    job_id=job_id,
                    error=msg,
                    attempts=attempt,
                )
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
                    strategy=strategy,
                    impersonate_target=impersonate_target,
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
                if is_permanent_error(exc):
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
                    log_event(
                        logger,
                        "download_failed_permanent",
                        job_id=job_id,
                        error=msg,
                        attempts=attempt,
                        strategy=strategy.name,
                    )
                    return {"job_id": job_id, "status": "failed", "error": msg}

                # Transient / setup / format: silent network retry, then strategy fallback
                can_net_retry = net_try < network_retries
                can_strategy = is_retryable_with_fallback(
                    exc, has_next_strategy=has_next
                )
                log_event(
                    logger,
                    "download_fallback",
                    job_id=job_id,
                    attempt=attempt,
                    net_try=net_try,
                    strategy=strategy.name,
                    can_net_retry=can_net_retry,
                    can_strategy=can_strategy,
                    error=f"{type(exc).__name__}: {_safe_err(exc)}"[:240],
                )
                if can_net_retry:
                    _retry_sleep(backoff, net_try)
                    _reset_tmp_dir(tmp_dir)
                    continue
                if can_strategy:
                    break  # next strategy
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
                log_event(
                    logger,
                    "download_failed",
                    job_id=job_id,
                    error=msg,
                    attempts=attempt,
                    strategy=strategy.name,
                )
                return {"job_id": job_id, "status": "failed", "error": msg}
        else:
            # network loop exhausted without break → continue outer attempts
            continue

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


def _safe_err(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__).strip()


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
            if getattr(job, "original_file_path", None):
                storage.delete_path(job.original_file_path)
                deleted_files += 1
            if getattr(job, "previous_file_path", None):
                storage.delete_path(job.previous_file_path)
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
