from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, status

from app.config import Settings
from app.logging_config import log_event

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify_captcha(token: str | None, ip: str, settings: Settings) -> None:
    if not settings.captcha_enabled:
        return

    if not settings.captcha_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CAPTCHA is enabled but not configured",
        )

    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CAPTCHA token required")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                TURNSTILE_VERIFY_URL,
                data={
                    "secret": settings.captcha_secret,
                    "response": token,
                    "remoteip": ip,
                },
            )
            data = resp.json()
    except Exception as exc:
        log_event(logger, "captcha_verify_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CAPTCHA verification unavailable",
        ) from exc

    if not data.get("success"):
        log_event(logger, "captcha_failed", ip=ip, codes=str(data.get("error-codes")))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CAPTCHA verification failed")
