from __future__ import annotations

import redis

from app.config import get_settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def ping_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except redis.RedisError:
        return False
