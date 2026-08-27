"""Per-key token bucket rate limiting — window behavior and Redis-down degradation."""
from app.services.rate_limiter import RateLimiter


async def test_allows_requests_under_the_limit():
    limiter = RateLimiter()
    for _ in range(5):
        result = await limiter.check(key_hash="k1", limit=5)
        assert result.allowed is True
    assert result.remaining == 0


async def test_blocks_once_limit_exceeded():
    limiter = RateLimiter()
    for _ in range(5):
        await limiter.check(key_hash="k1", limit=5)
    result = await limiter.check(key_hash="k1", limit=5)
    assert result.allowed is False
    assert result.remaining == 0
    assert result.reset_seconds > 0


async def test_keys_are_isolated_from_each_other():
    limiter = RateLimiter()
    for _ in range(5):
        await limiter.check(key_hash="k1", limit=5)
    result = await limiter.check(key_hash="k2", limit=5)
    assert result.allowed is True, "a different key must have its own independent budget"


async def test_current_usage_reflects_consumed_tokens():
    limiter = RateLimiter()
    assert await limiter.current_usage(key_hash="k1") == 0
    await limiter.check(key_hash="k1", limit=100)
    await limiter.check(key_hash="k1", limit=100)
    assert await limiter.current_usage(key_hash="k1") == 2


async def test_redis_unreachable_degrades_to_allow(monkeypatch):
    """Availability of the gateway matters more than strict enforcement of
    a limit whose backing store is down."""
    monkeypatch.setattr("app.services.rate_limiter.get_redis", lambda: _FailingRedis())
    limiter = RateLimiter()
    result = await limiter.check(key_hash="k1", limit=1)
    assert result.allowed is True
    assert result.remaining == 1  # full budget reported back, not partial/zero


class _FailingRedis:
    def __getattr__(self, _name):
        async def _raise(*_a, **_k):
            import redis.exceptions
            raise redis.exceptions.ConnectionError("simulated redis outage")
        return _raise

    def pipeline(self):
        return self

    def incr(self, *_a, **_k):
        return self

    def expire(self, *_a, **_k):
        return self

    async def execute(self):
        import redis.exceptions
        raise redis.exceptions.ConnectionError("simulated redis outage")
