"""Runtime download capability probes (impersonate, cookies)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.ytdlp_support import resolve_impersonate_target

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadCapabilities:
    impersonate_available: bool
    cookies_configured: bool
    cookies_readable: bool
    facebook_ready: bool
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "impersonate_available": self.impersonate_available,
            "cookies_configured": self.cookies_configured,
            "cookies_readable": self.cookies_readable,
            "facebook_ready": self.facebook_ready,
            "warnings": list(self.warnings),
        }


def _cookies_status(settings: Settings) -> tuple[bool, bool, str | None]:
    """Return (configured, readable, warning)."""
    raw = (settings.ytdlp_cookies_file or "").strip()
    if not raw:
        return False, False, None
    path = Path(raw)
    if not path.is_file():
        return True, False, f"YTDLP_COOKIES_FILE is set but not readable: {raw}"
    try:
        if path.stat().st_size <= 0:
            return True, False, "YTDLP_COOKIES_FILE exists but is empty"
    except OSError as exc:
        return True, False, f"YTDLP_COOKIES_FILE cannot be read: {exc}"
    return True, True, None


def probe_download_capabilities(settings: Settings) -> DownloadCapabilities:
    impersonate = resolve_impersonate_target() is not None
    cookies_configured, cookies_readable, cookie_warn = _cookies_status(settings)

    warnings: list[str] = []
    if not impersonate:
        warnings.append(
            "Browser impersonation unavailable (install curl_cffi). "
            "Facebook downloads may fail more often."
        )
    if cookie_warn:
        warnings.append(cookie_warn)

    # Facebook is most reliable with impersonate; cookies help picky / soft-gated posts.
    facebook_ready = impersonate or cookies_readable
    if not facebook_ready:
        warnings.append(
            "Facebook readiness is low: enable curl_cffi impersonation "
            "and/or a server-side cookies.txt for better success rates."
        )

    return DownloadCapabilities(
        impersonate_available=impersonate,
        cookies_configured=cookies_configured,
        cookies_readable=cookies_readable,
        facebook_ready=facebook_ready,
        warnings=warnings,
    )


def log_download_capabilities(settings: Settings) -> DownloadCapabilities:
    caps = probe_download_capabilities(settings)
    logger.info(
        "download_capabilities impersonate=%s cookies=%s/%s facebook_ready=%s",
        caps.impersonate_available,
        caps.cookies_configured,
        caps.cookies_readable,
        caps.facebook_ready,
    )
    for w in caps.warnings:
        logger.warning("download_capability_warning: %s", w)
    return caps
