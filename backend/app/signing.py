from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from fastapi import HTTPException, status

from app.config import Settings


def create_signed_download_token(
    job_id: str,
    opaque_token: str,
    filename: str,
    settings: Settings,
) -> tuple[str, int]:
    expires = int(time.time()) + settings.download_url_ttl_seconds
    payload = f"{job_id}:{opaque_token}:{expires}:{filename}"
    signature = hmac.new(
        settings.download_signing_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = f"{expires}.{signature}"
    return token, settings.download_url_ttl_seconds


def verify_signed_download_token(
    job_id: str,
    opaque_token: str,
    filename: str,
    token: str,
    settings: Settings,
) -> None:
    try:
        expires_str, signature = token.split(".", 1)
        expires = int(expires_str)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid download token")

    if time.time() > expires:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Download link expired")

    payload = f"{job_id}:{opaque_token}:{expires}:{filename}"
    expected = hmac.new(
        settings.download_signing_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid download token")


def build_download_path(job_id: str, token: str, filename: str) -> str:
    return f"/api/jobs/{job_id}/file?token={quote(token)}&name={quote(filename)}"
