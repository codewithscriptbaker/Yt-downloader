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
    url: str | None = None,
) -> list[DownloadStrategy]:
    """
    Production fallback ladder:

    YouTube / Instagram / TikTok:
      1. Default clients → 2. Impersonate → 3. Soft format

    Facebook (impersonate-first — FB fingerprinting often breaks without it):
      1. Impersonate → 2. Standard → 3. Soft progressive format
    """
    q = (quality or "best").lower()
    facebook = bool(url and is_facebook_url(url))

    if facebook:
        strategies: list[DownloadStrategy] = []
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
        strategies.append(
            DownloadStrategy(
                name="standard",
                use_impersonate=False,
                player_clients=(),
                soft_format=False,
                message="Fetching media…",
            )
        )
        if q != "audio":
            strategies.append(
                DownloadStrategy(
                    name="compat",
                    use_impersonate=impersonate_available,
                    player_clients=(),
                    soft_format=True,
                    message="Retrying with compatible format…",
                )
            )
        return strategies

    strategies = [
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
    # Facebook often requires chrome impersonate to parse webpage/API payloads.
    if url and is_facebook_url(url) and impersonate_target is not None:
        if strategy is None or strategy.use_impersonate:
            use_imp = True
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
        apply_site_compat(ydl_opts, url, impersonate_target=impersonate_target)

    return ydl_opts


def _url_host(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_tiktok_url(url: str) -> bool:
    host = _url_host(url)
    if host:
        return "tiktok.com" in host
    return "tiktok.com" in (url or "").lower()


def is_facebook_url(url: str) -> bool:
    host = _url_host(url)
    if not host:
        lowered = (url or "").lower()
        return any(t in lowered for t in ("facebook.com", "fb.watch", "fb.com"))
    return (
        "facebook.com" in host
        or host == "fb.watch"
        or host.endswith(".fb.watch")
        or host == "fb.com"
        or host.endswith(".fb.com")
    )


def apply_site_compat(
    ydl_opts: dict[str, Any],
    url: str,
    *,
    impersonate_target: Any | None = None,
) -> dict[str, Any]:
    """
    Site-specific yt-dlp tweaks.

    TikTok: chrome impersonate often triggers JS challenges — disable it.
    Facebook: prefer chrome impersonate + slightly friendlier retries/headers.
    """
    if is_tiktok_url(url):
        ydl_opts.pop("impersonate", None)
        return ydl_opts

    if is_facebook_url(url):
        # Prefer progressive / mobile-friendly Accept when FB is picky.
        headers = dict(ydl_opts.get("http_headers") or {})
        headers.setdefault(
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        ydl_opts["http_headers"] = headers
        # Extra fragment retries help flaky FB CDN edges.
        ydl_opts.setdefault("retries", 10)
        ydl_opts.setdefault("fragment_retries", 10)
        return ydl_opts

    return ydl_opts
