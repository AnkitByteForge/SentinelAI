# Per-API-key rate limiting — fixed 60s window token bucket backed by Redis.
from __future__ import annotations

import time
from dataclasses import dataclass

import redis.exceptions

from app.services.redis_client import get_redis


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int


class RateLimiter:
    """
    Token bucket keyed by "ratelimit:{key_hash}:{window_minute}".
    Each 60s window starts with `limit` tokens; each request consumes one
    via INCR. If Redis is unreachable, requests are allowed through —
    availability of the gateway matters more than strict enforcement of a
    limit whose backing store is down.
    """

    WINDOW_SECONDS = 60

    async def check(self, key_hash: str, limit: int) -> RateLimitResult:
        now = int(time.time())
        window = now // self.WINDOW_SECONDS
        reset_seconds = self.WINDOW_SECONDS - (now % self.WINDOW_SECONDS)
        redis_key = f"ratelimit:{key_hash}:{window}"

        try:
            r = get_redis()
            pipe = r.pipeline()
            pipe.incr(redis_key, 1)
            pipe.expire(redis_key, self.WINDOW_SECONDS, nx=True)
            used, _ = await pipe.execute()
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            return RateLimitResult(allowed=True, limit=limit, remaining=limit, reset_seconds=reset_seconds)

        used = int(used)
        remaining = max(0, limit - used)
        return RateLimitResult(allowed=used <= limit, limit=limit, remaining=remaining, reset_seconds=reset_seconds)

    async def current_usage(self, key_hash: str) -> int:
        """Tokens consumed in the current window — used for GET /v1/keys stats."""
        now = int(time.time())
        window = now // self.WINDOW_SECONDS
        redis_key = f"ratelimit:{key_hash}:{window}"
        try:
            r = get_redis()
            val = await r.get(redis_key)
            return int(val) if val else 0
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            return 0


limiter = RateLimiter()
