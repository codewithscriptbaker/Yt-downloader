"""Media metadata preview (yt-dlp extract_info, no download)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.config import Settings
from app.errors import map_ytdlp_error
from app.models import PreviewResponse

logger = logging.getLogger(__name__)


def _impersonate_target() -> str | None:
    try:
        import curl_cffi  # noqa: F401

        return "chrome"
    except ImportError:
        return None


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


def fetch_preview(url: str, settings: Settings) -> PreviewResponse:
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": settings.download_socket_timeout,
        "extractor_args": {
            "youtube": {"player_client": ["android", "ios", "web"]},
        },
    }
    impersonate = _impersonate_target()
    if impersonate:
        ydl_opts["impersonate"] = impersonate

    cookies = (settings.ytdlp_cookies_file or "").strip()
    if cookies:
        ydl_opts["cookiefile"] = cookies

    proxy = (settings.ytdlp_proxy or "").strip()
    if proxy:
        ydl_opts["proxy"] = proxy

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise DownloadError(map_ytdlp_error(exc)) from exc

    if info is None:
        raise DownloadError("No media information returned")

    if "entries" in info and info["entries"]:
        info = info["entries"][0]

    duration = info.get("duration")
    duration_seconds = int(duration) if isinstance(duration, (int, float)) else None

    return PreviewResponse(
        title=(str(info["title"])[:200] if info.get("title") else None),
        thumbnail=_pick_thumbnail(info),
        duration_seconds=duration_seconds,
        uploader=(str(info["uploader"])[:120] if info.get("uploader") else None),
        site=(str(info.get("extractor_key") or info.get("extractor") or "")[:60] or None),
        estimated_size_mb=_estimate_size_mb(info),
        url=url,
    )
