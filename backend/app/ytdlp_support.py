"""Shared yt-dlp helpers: impersonate resolution + download fallback strategies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadStrategy:
    """One attempt profile in the download fallback ladder."""

    name: str
    use_impersonate: bool
    # Empty tuple = yt-dlp default clients (needed for full YouTube quality list).
    player_clients: tuple[str, ...]
    soft_format: bool  # prefer a safer height cap when primary format fails
    message: str


def resolve_impersonate_target() -> Any | None:
    """
    Return a yt-dlp ImpersonateTarget when curl_cffi + target are usable.

    Never returns a bare string — that raises AssertionError on modern yt-dlp.
    """
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return None

    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.networking.impersonate import ImpersonateTarget

        target = ImpersonateTarget(client="chrome")
        probe = YoutubeDL({"quiet": True, "no_warnings": True})
        if not probe._impersonate_target_available(target):
            logger.info("impersonate_unavailable", extra={"target": str(target)})
            return None
        return target
    except Exception as exc:
        logger.info(
            "impersonate_resolve_failed",
            extra={"error": f"{type(exc).__name__}: {exc}"[:200]},
        )
        return None


def build_download_strategies(
    *,
    quality: str,
    impersonate_available: bool,
) -> list[DownloadStrategy]:
    """
    Production fallback ladder:

    1. Default yt-dlp clients (full YouTube format ladder)
    2. Impersonate when available (helps some sites / bot checks)
    3. Compat: android/web + softer height
    """
    q = (quality or "best").lower()
    strategies: list[DownloadStrategy] = [
        DownloadStrategy(
            name="standard",
            use_impersonate=False,
            player_clients=(),  # default — exposes 720/1080/etc on YouTube
            soft_format=False,
            message="Fetching media…",
        )
    ]

    if impersonate_available:
        strategies.append(
            DownloadStrategy(
                name="impersonate",
                use_impersonate=True,
                player_clients=(),
                soft_format=False,
                message="Fetching media (secure client)…",
            )
        )

    if q != "audio":
        strategies.append(
            DownloadStrategy(
                name="compat",
                use_impersonate=False,
                player_clients=("android", "web"),
                soft_format=True,
                message="Retrying with compatible format…",
            )
        )

    return strategies


def effective_quality(quality: str, strategy: DownloadStrategy) -> str:
    """Map requested quality through soft-format fallback when needed."""
    from app.formats import normalize_quality

    q = normalize_quality(quality)
    if not strategy.soft_format or q == "audio":
        return q
    if q == "best":
        return "720"
    if q.isdigit():
        h = int(q)
        if h >= 1080:
            return "720"
        if h >= 720:
            return "360"
        return q
    return q


def apply_common_opts(
    ydl_opts: dict[str, Any],
    *,
    settings,
    strategy: Optional[DownloadStrategy] = None,
    impersonate_target: Any | None = None,
    player_clients: Sequence[str] | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Attach cookies/proxy/impersonate/optional player clients onto yt-dlp opts."""
    if player_clients is not None:
        clients = list(player_clients)
    elif strategy is not None:
        clients = list(strategy.player_clients)
    else:
        clients = []

    # Only force YouTube player_client when explicitly requested.
    # Empty/default preserves the full quality ladder on modern yt-dlp.
    if clients:
        ydl_opts.setdefault("extractor_args", {})
        ydl_opts["extractor_args"] = {
            **ydl_opts.get("extractor_args", {}),
            "youtube": {"player_client": clients},
        }
    else:
        # Drop any previous youtube player_client override
        existing = ydl_opts.get("extractor_args")
        if isinstance(existing, dict) and "youtube" in existing:
            yt_args = dict(existing.get("youtube") or {})
            yt_args.pop("player_client", None)
            merged = {**existing}
            if yt_args:
                merged["youtube"] = yt_args
            else:
                merged.pop("youtube", None)
            if merged:
                ydl_opts["extractor_args"] = merged
            else:
                ydl_opts.pop("extractor_args", None)

    use_imp = bool(strategy.use_impersonate) if strategy else bool(impersonate_target)
    # TikTok challenge pages often break under chrome impersonate.
    if url and is_tiktok_url(url):
        use_imp = False
    if use_imp and impersonate_target is not None:
        ydl_opts["impersonate"] = impersonate_target
    else:
        ydl_opts.pop("impersonate", None)

    cookies = (settings.ytdlp_cookies_file or "").strip()
    if cookies:
        ydl_opts["cookiefile"] = cookies

    proxy = (settings.ytdlp_proxy or "").strip()
    if proxy:
        ydl_opts["proxy"] = proxy

    if url:
        apply_site_compat(ydl_opts, url)

    return ydl_opts


def is_tiktok_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return "tiktok.com" in (url or "").lower()
    return "tiktok.com" in host


def apply_site_compat(ydl_opts: dict[str, Any], url: str) -> dict[str, Any]:
    """
    Site-specific yt-dlp tweaks.

    TikTok often serves a JS challenge when chrome impersonate is used, which
    surfaces as "Unable to extract universal data for rehydration". Prefer the
    default client (no impersonate) for TikTok URLs.
    """
    if not is_tiktok_url(url):
        return ydl_opts

    ydl_opts.pop("impersonate", None)
    return ydl_opts
