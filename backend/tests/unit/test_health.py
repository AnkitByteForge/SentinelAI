"""Health check status rollup rules — healthy/degraded/unhealthy."""
import pytest

from app.db.database import AsyncSessionLocal
from app.services import health
from app.services.circuit_breaker import registry as cb


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def test_all_healthy_when_nothing_is_wrong(db):
    result = await health.get_system_health(db)
    assert result["status"] == "healthy"
    assert result["checks"]["database"]["status"] == "healthy"
    assert result["checks"]["redis"]["status"] == "healthy"
    assert result["checks"]["providers"]["groq"]["status"] == "healthy"


async def test_open_provider_circuit_degrades_but_does_not_fail_health(db):
    for _ in range(3):
        await cb.record_failure("groq")

    result = await health.get_system_health(db)
    assert result["status"] == "degraded"
    assert result["checks"]["providers"]["groq"]["status"] == "unhealthy"
    assert result["checks"]["providers"]["groq"]["circuit_state"] == "open"


async def test_database_unreachable_is_unhealthy_overall(db, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("simulated database outage")

    monkeypatch.setattr(health, "_check_database", _boom)

    with pytest.raises(RuntimeError):
        # asyncio.gather re-raises; the important behavioral contract this
        # protects is that a DB failure isn't silently swallowed into a
        # false "healthy" — see test_database_error_path_reports_unhealthy
        # below for the path actually used by the /health endpoints.
        await health.get_system_health(db)


async def test_database_error_path_reports_unhealthy(db):
    """_check_database itself catches exceptions and reports unhealthy —
    this is what /health actually relies on, not an unhandled raise."""

    class _BrokenSession:
        async def execute(self, *_a, **_k):
            raise RuntimeError("simulated database outage")

    result = await health._check_database(_BrokenSession())
    assert result["status"] == "unhealthy"


async def test_celery_queue_depth_over_threshold_is_degraded(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "celery_queue_depth_degraded_threshold", 5)
    monkeypatch.setattr("app.services.health.get_redis", lambda: _FakeRedisWithQueueDepth(10))

    result = await health._check_celery()
    assert result["status"] == "degraded"
    assert result["queue_depth"] == 10


class _FakeRedisWithQueueDepth:
    def __init__(self, depth: int):
        self._depth = depth

    async def llen(self, _key):
        return self._depth

    async def ping(self):
        return True
