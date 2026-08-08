from __future__ import annotations

import logging
import re
from typing import Union

logger = logging.getLogger(__name__)


ERROR_MAP = [
    (re.compile(r"private video|login required|sign in", re.I), "This video is private or requires login."),
    (re.compile(r"geo.?restrict|not available in your country|blocked in your country", re.I), "This media is geo-blocked in this region."),
    (re.compile(r"video unavailable|unavailable|removed|deleted", re.I), "This media is unavailable or has been removed."),
    (re.compile(r"no video formats|requested format is not available", re.I), "Could not find a downloadable format for this media."),
    (re.compile(r"copyright|dmca", re.I), "This media cannot be downloaded due to restrictions."),
    (re.compile(r"timed? ?out|timeout|socket.?timeout|read timed out", re.I), "Download timed out. Try a shorter video or try again."),
    (re.compile(r"file is larger|max.?filesize|too large", re.I), "File exceeds the maximum allowed size."),
    (re.compile(r"http error 429|too many requests", re.I), "The site is rate-limiting downloads. Try again later."),
    (re.compile(r"longer than .+ minutes is not allowed", re.I), "This video is longer than the allowed duration limit."),
    (re.compile(r"unsupported url|no suitable extractor", re.I), "This link isn’t from a supported site."),
    (
        re.compile(
            r"unable to extract universal data|unable to extract webpage video data|"
            r"unexpected response from webpage|rehydration",
            re.I,
        ),
        "TikTok blocked this preview. Try again in a moment, or paste a different public link.",
    ),
    (
        re.compile(
            r"getaddrinfo failed|name or service not known|nodename nor servname|"
            r"temporary failure in name resolution|dns|failed to resolve|"
            r"could not resolve host|curl:\s*\(6\)|"
            r"connection (refused|reset|aborted)|network is unreachable|"
            r"no route to host|ssl|eof occurred|remote end closed|"
            r"http error 5\d\d|server error|unable to download webpage|"
            r"urlopen error|connection error|network unreachable",
            re.I,
        ),
        "Couldn't reach the site right now. Please try again in a moment.",
    ),
]

# Short next-step hints keyed by the same friendly messages (or pattern).
ERROR_HINTS = [
    (re.compile(r"private|login|sign in", re.I), "Only public media can be downloaded. Try a different public link."),
    (re.compile(r"geo-blocked|country", re.I), "This title isn’t available from our region. A different link may work."),
    (re.compile(r"unavailable|removed", re.I), "Check the link is still live, then try again."),
    (re.compile(r"downloadable format|format", re.I), "Try another quality (e.g. 720 or Audio only), then retry."),
    (re.compile(r"timed out|timeout", re.I), "Retry with a lower quality, or try again when the network is steadier."),
    (re.compile(r"maximum allowed size|larger", re.I), "Try a lower quality, or a shorter clip."),
    (re.compile(r"rate-limiting|try again later", re.I), "Wait a few minutes, then retry the same link."),
    (re.compile(r"duration limit|longer than", re.I), "Pick a shorter video under the duration cap."),
    (re.compile(r"supported site", re.I), "Use a YouTube, TikTok, or Instagram link."),
    (re.compile(r"TikTok blocked|rehydration", re.I), "Open the clip in a browser to confirm it’s public, then paste the link again."),
    (re.compile(r"Couldn't reach the site", re.I), "Check your connection, then tap Retry."),
]

# Errors that are usually short-lived — retry instead of failing the job immediately.
TRANSIENT_PATTERNS = [
    re.compile(r"could not resolve host|curl:\s*\(6\)", re.I),
    re.compile(r"getaddrinfo failed", re.I),
    re.compile(r"failed to resolve", re.I),
    re.compile(r"name or service not known", re.I),
    re.compile(r"nodename nor servname", re.I),
    re.compile(r"temporary failure in name resolution", re.I),
    re.compile(r"errno 11001|errno 11002|errno -2|errno -3", re.I),
    re.compile(r"connection (refused|reset|aborted|timed? ?out)", re.I),
    re.compile(r"network is unreachable|no route to host", re.I),
    re.compile(r"urlopen error", re.I),
    re.compile(r"unable to download webpage", re.I),
    re.compile(r"http error 429|too many requests", re.I),
    re.compile(r"http error 5\d\d", re.I),
    re.compile(r"timed? ?out|timeout|socket.?timeout|read timed out", re.I),
    re.compile(r"ssl|eof occurred|remote end closed", re.I),
    re.compile(r"temporary failure|try again later", re.I),
]

# Permanent failures — never retry / never advance fallback ladder.
PERMANENT_PATTERNS = [
    re.compile(r"private video|login required|sign in", re.I),
    re.compile(r"geo.?restrict|not available in your country|blocked in your country", re.I),
    re.compile(r"video unavailable|has been removed|uploader has closed", re.I),
    re.compile(r"copyright|dmca", re.I),
    re.compile(r"file is larger|max.?filesize|too large", re.I),
    re.compile(r"longer than .+ minutes is not allowed", re.I),
    re.compile(r"unsupported url|no suitable extractor", re.I),
]

# Format issues may succeed on a softer strategy — not hard-permanent for the ladder.
FORMAT_FALLBACK_PATTERNS = [
    re.compile(r"no video formats|requested format is not available", re.I),
    re.compile(r"format is not available", re.I),
]

# Client / extractor setup failures that should advance the fallback ladder.
SETUP_FALLBACK_PATTERNS = [
    re.compile(r"assertionerror", re.I),
    re.compile(r"impersonate", re.I),
    re.compile(r"curl_cffi", re.I),
    re.compile(r"impersonate.?target", re.I),
]


def _message_of(exc: Union[BaseException, str]) -> str:
    if isinstance(exc, BaseException):
        return str(exc) or exc.__class__.__name__
    return str(exc)


def is_permanent_error(exc: Union[BaseException, str]) -> bool:
    message = _message_of(exc)
    if any(p.search(message) for p in FORMAT_FALLBACK_PATTERNS):
        return False
    return any(p.search(message) for p in PERMANENT_PATTERNS)


def is_setup_fallback_error(exc: Union[BaseException, str]) -> bool:
    """True for client/impersonate/setup failures that another strategy may fix."""
    if isinstance(exc, BaseException) and type(exc).__name__ == "AssertionError":
        return True
    message = _message_of(exc)
    return any(p.search(message) for p in SETUP_FALLBACK_PATTERNS)


def is_format_fallback_error(exc: Union[BaseException, str]) -> bool:
    message = _message_of(exc)
    return any(p.search(message) for p in FORMAT_FALLBACK_PATTERNS)


def is_transient_error(exc: Union[BaseException, str]) -> bool:
    """Return True when the failure is likely short-lived and worth retrying."""
    if is_permanent_error(exc):
        return False
    message = _message_of(exc)
    # Common network exception types from urllib / socket / yt-dlp wrappers
    if isinstance(exc, BaseException):
        name = type(exc).__name__.lower()
        if any(
            token in name
            for token in (
                "timeout",
                "connection",
                "ssl",
                "urlerror",
                "httperror",
                "oserror",
                "gaierror",
            )
        ):
            # Still exclude permanent yt-dlp content errors wrapped oddly
            if is_permanent_error(message):
                return False
            return True
    return any(p.search(message) for p in TRANSIENT_PATTERNS)


def is_retryable_with_fallback(
    exc: Union[BaseException, str],
    *,
    has_next_strategy: bool,
) -> bool:
    """
    Whether the download loop should advance to the next fallback strategy.

    Permanent content errors never retry. Transient / setup / format issues do
    when another strategy remains.
    """
    if is_permanent_error(exc):
        return False
    if not has_next_strategy:
        return False
    if is_transient_error(exc):
        return True
    if is_setup_fallback_error(exc):
        return True
    if is_format_fallback_error(exc):
        return True
    # Unknown errors: still try remaining strategies once (production resilience).
    return True


def map_ytdlp_error(exc: BaseException) -> str:
    message = _message_of(exc)
    if isinstance(exc, AssertionError) or message.lower() == "assertionerror":
        return "Download client setup failed. Retrying with a different method usually helps."
    for pattern, friendly in ERROR_MAP:
        if pattern.search(message):
            return friendly
    if is_transient_error(message):
        return "Couldn't reach the site right now. Please try again in a moment."
    # Keep message short for UI — avoid dumping raw stack/errno noise
    cleaned = message.strip().split("\n")[0][:240]
    return cleaned or "Download failed."


def hint_for_error(friendly_or_raw: str | None) -> str | None:
    """Return a short next-step hint for a user-facing error message."""
    if not friendly_or_raw:
        return None
    if "client setup failed" in friendly_or_raw.lower():
        return "Tap Retry — the next attempt uses a safer download method."
    for pattern, hint in ERROR_HINTS:
        if pattern.search(friendly_or_raw):
            return hint
    return "You can retry this link, or try a different quality."
