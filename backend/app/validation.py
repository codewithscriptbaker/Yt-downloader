from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.config import Settings


def _host_allowed(host: str, allowed: set[str]) -> bool:
    """Exact match or subdomain of an allow-listed root (e.g. m.facebook.com)."""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if host in allowed:
        return True
    for domain in allowed:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def validate_media_url(url: str, settings: Settings) -> tuple[str, str]:
    """Validate URL format and domain allow-list. Returns (normalized_url, domain)."""
    raw = (url or "").strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL is required")

    if len(raw) > 2048:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL is too long")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL must start with http:// or https://",
        )

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid URL host")

    if not _host_allowed(host, settings.allowed_domain_set):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported site. Allowed: YouTube, TikTok, Instagram, Facebook.",
        )

    return raw, host
