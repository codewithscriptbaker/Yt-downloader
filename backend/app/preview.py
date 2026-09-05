"""Media metadata preview (yt-dlp extract_info, no download)."""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.config import Settings
from app.errors import is_permanent_error, map_ytdlp_error
from app.formats import summarize_available_qualities
from app.models import AvailableQuality, PlaylistEntry, PreviewResponse
from app.ytdlp_support import (
    apply_common_opts,
    is_facebook_url,
    resolve_impersonate_target,
)

logger = logging.getLogger(__name__)


def is_playlist_url(url: str) -> bool:
    """True for dedicated playlist pages (not watch?v=…&list=…)."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    qs = parse_qs(parsed.query)

    if "youtube" not in host and "youtu.be" not in host:
        return False
    if "/playlist" in path:
        return True
    # list= without a specific video id → playlist landing
    if "list" in qs and "v" not in qs and "youtu.be" not in host:
        return True
    return False


def _pick_thumbnail(info: dict[str, Any]) -> Optional[str]:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("http"):
        return thumb
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        best = max(
            (t for t in thumbs if isinstance(t, dict) and t.get("url")),
            key=lambda t: (t.get("height") or 0) * (t.get("width") or 0),
            default=None,
        )
        if best and isinstance(best.get("url"), str):
            return best["url"]
    return None


def _estimate_size_mb(info: dict[str, Any]) -> Optional[float]:
    for key in ("filesize", "filesize_approx"):
        val = info.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return round(val / (1024 * 1024), 1)
    requested = info.get("requested_formats")
    if isinstance(requested, list):
        total = 0
        found = False
        for fmt in requested:
            if not isinstance(fmt, dict):
                continue
            for key in ("filesize", "filesize_approx"):
                val = fmt.get(key)
                if isinstance(val, (int, float)) and val > 0:
                    total += val
                    found = True
                    break
        if found:
            return round(total / (1024 * 1024), 1)
    return None


def _base_ydl_opts(
    settings: Settings, *, use_impersonate: bool, url: str | None = None
) -> dict[str, Any]:
    # No format filter — we need the full formats[] list for quality picker.
    facebook = bool(url and is_facebook_url(url))
    socket_timeout = (
        getattr(settings, "facebook_socket_timeout", 60)
        if facebook
        else settings.download_socket_timeout
    )
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": socket_timeout,
    }
    if facebook:
        ydl_opts["retries"] = 10
    target = resolve_impersonate_target() if use_impersonate else None
    apply_common_opts(
        ydl_opts,
        settings=settings,
        impersonate_target=target if use_impersonate else None,
        player_clients=(),
        url=url,
    )
    if not use_impersonate:
        ydl_opts.pop("impersonate", None)
    return ydl_opts


def _extract_info(url: str, settings: Settings, *, use_impersonate: bool) -> dict[str, Any]:
    ydl_opts = _base_ydl_opts(settings, use_impersonate=use_impersonate, url=url)
    ydl_opts["noplaylist"] = True

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise DownloadError("No media information returned")
    if "entries" in info and info["entries"]:
        info = info["entries"][0]
    return info


def _extract_playlist_info(
    url: str, settings: Settings, *, use_impersonate: bool
) -> dict[str, Any]:
    ydl_opts = _base_ydl_opts(settings, use_impersonate=use_impersonate, url=url)
    ydl_opts["noplaylist"] = False
    ydl_opts["extract_flat"] = "in_playlist"
    ydl_opts["playlistend"] = settings.max_playlist_entries

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise DownloadError("No playlist information returned")
    return info


def _entry_watch_url(entry: dict[str, Any]) -> Optional[str]:
    for key in ("webpage_url", "original_url"):
        val = entry.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val

    eid = entry.get("id")
    url_field = entry.get("url")

    def _youtube_watch(video_id: str) -> str:
        return f"https://www.youtube.com/watch?v={video_id}"

    ie = str(entry.get("ie_key") or entry.get("extractor") or "").lower()
    is_yt = "youtube" in ie or ie in ("", "youtube")

    if isinstance(url_field, str) and url_field.startswith("http"):
        lower = url_field.lower()
        if "playlist" in lower and "watch" not in lower:
            pass  # fall through to id
        else:
            return url_field

    # Flat extracts often put the bare video id in url / id
    for candidate in (eid, url_field):
        if not isinstance(candidate, str) or not candidate:
            continue
        if candidate.startswith("http"):
            continue
        if candidate.startswith("PL") or candidate.startswith("UU"):
            continue
        if is_yt or len(candidate) == 11:
            return _youtube_watch(candidate)

    return None


def _build_playlist_entries(
    info: dict[str, Any], *, limit: int
) -> tuple[list[PlaylistEntry], int, bool]:
    raw_entries = info.get("entries") or []
    if not isinstance(raw_entries, list):
        raw_entries = []

    # playlist_count may be larger than returned entries when truncated
    reported = info.get("playlist_count")
    total_hint = reported if isinstance(reported, int) and reported > 0 else len(raw_entries)

    built: list[PlaylistEntry] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("availability") in ("private", "premium", "subscriber_only"):
            # Still list them; download may fail later — skip only if no id
            pass
        watch_url = _entry_watch_url(entry)
        eid = entry.get("id")
        if not watch_url:
            continue
        if not isinstance(eid, str) or not eid:
            eid = watch_url
        duration = entry.get("duration")
        duration_seconds = int(duration) if isinstance(duration, (int, float)) else None
        title = entry.get("title")
        if isinstance(title, str) and title.lower() in ("[private video]", "[deleted video]"):
            continue
        built.append(
            PlaylistEntry(
                id=str(eid)[:64],
                title=(str(title)[:200] if title else None),
                url=watch_url[:2048],
                thumbnail=_pick_thumbnail(entry),
                duration_seconds=duration_seconds,
                uploader=(
                    str(entry["uploader"])[:120]
                    if entry.get("uploader")
                    else (str(entry["channel"])[:120] if entry.get("channel") else None)
                ),
            )
        )
        if len(built) >= limit:
            break

    truncated = total_hint > len(built) or len(raw_entries) > len(built)
    return built, max(total_hint, len(built)), truncated


def _video_preview_from_info(info: dict[str, Any], url: str) -> PreviewResponse:
    duration = info.get("duration")
    duration_seconds = int(duration) if isinstance(duration, (int, float)) else None

    qualities_raw = summarize_available_qualities(info)
    qualities = [AvailableQuality.model_validate(q) for q in qualities_raw]
    max_height = None
    for q in qualities:
        if q.height is not None:
            max_height = q.height if max_height is None else max(max_height, q.height)

    return PreviewResponse(
        kind="video",
        title=(str(info["title"])[:200] if info.get("title") else None),
        thumbnail=_pick_thumbnail(info),
        duration_seconds=duration_seconds,
        uploader=(str(info["uploader"])[:120] if info.get("uploader") else None),
        site=(str(info.get("extractor_key") or info.get("extractor") or "")[:60] or None),
        estimated_size_mb=_estimate_size_mb(info),
        url=url,
        available_qualities=qualities,
        max_height=max_height,
    )


def _default_playlist_qualities() -> list[AvailableQuality]:
    return [
        AvailableQuality(id="best", label="Best", has_video=True, has_audio=True),
        AvailableQuality(id="1080", label="1080p", height=1080, has_video=True, has_audio=True),
        AvailableQuality(id="720", label="720p", height=720, has_video=True, has_audio=True),
        AvailableQuality(id="360", label="360p", height=360, has_video=True, has_audio=True),
        AvailableQuality(id="audio", label="Audio only", has_video=False, has_audio=True),
    ]


def _try_first_entry_qualities(
    entries: list[PlaylistEntry], settings: Settings, *, use_impersonate: bool
) -> tuple[list[AvailableQuality], Optional[int]]:
    if not entries:
        return _default_playlist_qualities(), None
    try:
        info = _extract_info(entries[0].url, settings, use_impersonate=use_impersonate)
        qualities_raw = summarize_available_qualities(info)
        qualities = [AvailableQuality.model_validate(q) for q in qualities_raw]
        if not qualities:
            return _default_playlist_qualities(), None
        max_height = None
        for q in qualities:
            if q.height is not None:
                max_height = q.height if max_height is None else max(max_height, q.height)
        return qualities, max_height
    except Exception as exc:
        logger.info(
            "playlist_quality_probe_failed",
            extra={"error": f"{type(exc).__name__}: {exc}"[:200]},
        )
        return _default_playlist_qualities(), None


def _with_retries(extract_fn, url: str, settings: Settings) -> dict[str, Any]:
    last_exc: BaseException | None = None
    # Facebook needs impersonate first when available (fingerprint / parse failures).
    if is_facebook_url(url) and resolve_impersonate_target() is not None:
        attempts = (True, False)
    elif resolve_impersonate_target() is not None:
        attempts = (True, False)
    else:
        attempts = (False,)
    for use_impersonate in attempts:
        try:
            return extract_fn(url, settings, use_impersonate=use_impersonate)
        except Exception as exc:
            last_exc = exc
            if is_permanent_error(exc):
                raise DownloadError(map_ytdlp_error(exc)) from exc
            logger.info(
                "preview_fallback",
                extra={
                    "use_impersonate": use_impersonate,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                },
            )
            continue
    raise DownloadError(map_ytdlp_error(last_exc or DownloadError("Preview failed")))


def fetch_preview(url: str, settings: Settings) -> PreviewResponse:
    if is_playlist_url(url):
        info = _with_retries(_extract_playlist_info, url, settings)
        entries, playlist_count, truncated = _build_playlist_entries(
            info, limit=settings.max_playlist_entries
        )
        if not entries:
            raise DownloadError("This playlist has no downloadable videos.")

        use_imp = resolve_impersonate_target() is not None
        qualities, max_height = _try_first_entry_qualities(
            entries, settings, use_impersonate=use_imp
        )

        return PreviewResponse(
            kind="playlist",
            title=(str(info["title"])[:200] if info.get("title") else "Playlist"),
            thumbnail=_pick_thumbnail(info) or entries[0].thumbnail,
            duration_seconds=None,
            uploader=(
                str(info["uploader"])[:120]
                if info.get("uploader")
                else (str(info["channel"])[:120] if info.get("channel") else None)
            ),
            site=(str(info.get("extractor_key") or info.get("extractor") or "")[:60] or None),
            estimated_size_mb=None,
            url=url,
            available_qualities=qualities,
            max_height=max_height,
            playlist_count=playlist_count,
            entries=entries,
            max_playlist_select=settings.max_playlist_select,
            entries_truncated=truncated,
        )

    info = _with_retries(_extract_info, url, settings)
    return _video_preview_from_info(info, url)
