# Shared async Redis client — used by api_keys, rate_limiter, and health checks.
from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Returns the process-wide async Redis client, creating it on first use."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


async def aclose_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
