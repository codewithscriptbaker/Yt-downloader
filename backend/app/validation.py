from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.config import Settings


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

    # Strip leading www. for matching, but also check exact host
    allowed = settings.allowed_domain_set
    if host not in allowed:
        # Allow subdomain matches for listed roots (e.g. m.youtube.com already listed)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported site. Allowed: YouTube, TikTok, Instagram.",
        )

    return raw, host
