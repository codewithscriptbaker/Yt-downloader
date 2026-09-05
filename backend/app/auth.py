"""Optional user accounts (Redis-backed). Downloads work without auth."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from app.config import Settings, get_settings
from app.redis_client import get_redis

USER_EMAIL_KEY = "user:email:{email}"
USER_ID_KEY = "user:id:{user_id}"
USER_HISTORY_KEY = "user:history:{user_id}"

_bearer = HTTPBearer(auto_error=False)


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(default="", max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class AuthUser(BaseModel):
    user_id: str
    email: str
    name: str = ""


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser


class HistoryCreateRequest(BaseModel):
    source_url: str = Field(..., min_length=8, max_length=2048)
    title: str = Field(default="Download", max_length=300)
    thumbnail: Optional[str] = None
    quality: Optional[str] = None
    audio_format: Optional[str] = None
    file_name: Optional[str] = None
    file_size_mb: Optional[float] = None


class HistoryItemOut(BaseModel):
    id: str
    source_url: str
    title: str
    thumbnail: Optional[str] = None
    quality: Optional[str] = None
    audio_format: Optional[str] = None
    file_name: Optional[str] = None
    file_size_mb: Optional[float] = None
    completed_at: float


class HistoryListResponse(BaseModel):
    items: list[HistoryItemOut]


def _norm_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, email: str, settings: Settings) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + settings.auth_token_ttl_seconds,
    }
    return jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session. Please log in again.",
        ) from exc


def _user_from_record(raw: dict[str, Any]) -> AuthUser:
    return AuthUser(
        user_id=raw["user_id"],
        email=raw["email"],
        name=raw.get("name") or "",
    )


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    r = get_redis()
    data = r.get(USER_EMAIL_KEY.format(email=_norm_email(email)))
    if not data:
        return None
    return json.loads(data)


def get_user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    r = get_redis()
    email = r.get(USER_ID_KEY.format(user_id=user_id))
    if not email:
        return None
    if isinstance(email, bytes):
        email = email.decode("utf-8")
    return get_user_by_email(email)


def create_user(*, email: str, password: str, name: str = "") -> dict[str, Any]:
    email_n = _norm_email(email)
    if get_user_by_email(email_n):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    user_id = str(uuid.uuid4())
    record = {
        "user_id": user_id,
        "email": email_n,
        "name": (name or "").strip()[:80],
        "password_hash": hash_password(password),
        "created_at": time.time(),
    }
    r = get_redis()
    pipe = r.pipeline()
    pipe.set(USER_EMAIL_KEY.format(email=email_n), json.dumps(record))
    pipe.set(USER_ID_KEY.format(user_id=user_id), email_n)
    pipe.execute()
    return record


def authenticate_user(email: str, password: str) -> dict[str, Any]:
    user = get_user_by_email(email)
    if not user or not verify_password(password, user.get("password_hash", "")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    return user


def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Optional[AuthUser]:
    """Never blocks anonymous requests — returns None if no/invalid token."""
    if not credentials or not credentials.credentials:
        return None
    try:
        payload = decode_access_token(credentials.credentials, settings)
        user = get_user_by_id(str(payload.get("sub") or ""))
        if not user:
            return None
        return _user_from_record(user)
    except HTTPException:
        return None


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Log in to use this feature.",
        )
    payload = decode_access_token(credentials.credentials, settings)
    user = get_user_by_id(str(payload.get("sub") or ""))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found. Please sign up again.",
        )
    return _user_from_record(user)


def list_history(user_id: str, *, limit: int = 50) -> list[HistoryItemOut]:
    r = get_redis()
    key = USER_HISTORY_KEY.format(user_id=user_id)
    raw_items = r.lrange(key, 0, max(0, limit - 1))
    out: list[HistoryItemOut] = []
    for raw in raw_items or []:
        try:
            data = json.loads(raw)
            out.append(HistoryItemOut.model_validate(data))
        except Exception:
            continue
    return out


def add_history(user_id: str, item: HistoryCreateRequest) -> HistoryItemOut:
    entry = HistoryItemOut(
        id=str(uuid.uuid4()),
        source_url=item.source_url.strip(),
        title=(item.title or "Download").strip()[:300] or "Download",
        thumbnail=item.thumbnail,
        quality=item.quality,
        audio_format=item.audio_format,
        file_name=item.file_name,
        file_size_mb=item.file_size_mb,
        completed_at=time.time(),
    )
    r = get_redis()
    key = USER_HISTORY_KEY.format(user_id=user_id)
    existing = r.lrange(key, 0, 49) or []
    filtered: list[str] = []
    for raw in existing:
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        try:
            data = json.loads(text)
            if (
                data.get("source_url") == entry.source_url
                and data.get("quality") == entry.quality
            ):
                continue
        except Exception:
            pass
        filtered.append(text)

    pipe = r.pipeline()
    pipe.delete(key)
    pipe.rpush(key, entry.model_dump_json(), *filtered[:49])
    pipe.execute()
    return entry


def clear_history(user_id: str) -> None:
    get_redis().delete(USER_HISTORY_KEY.format(user_id=user_id))


def delete_history_item(user_id: str, item_id: str) -> None:
    r = get_redis()
    key = USER_HISTORY_KEY.format(user_id=user_id)
    items = r.lrange(key, 0, -1) or []
    kept: list[str] = []
    for raw in items:
        text = raw if isinstance(raw, str) else raw.decode("utf-8")
        try:
            data = json.loads(text)
            if data.get("id") == item_id:
                continue
        except Exception:
            pass
        kept.append(text)
    pipe = r.pipeline()
    pipe.delete(key)
    if kept:
        pipe.rpush(key, *kept)
    pipe.execute()
