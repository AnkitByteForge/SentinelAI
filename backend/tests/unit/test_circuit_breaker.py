"""
Circuit breaker state machine — CLOSED/OPEN/HALF_OPEN transitions, and that
webhook notifications fire only on state transitions, not on every failure.
"""
import time

import pytest

from app.services.circuit_breaker import FAILURE_THRESHOLD, RESET_TIMEOUT_SEC, CircuitBreakerRegistry


@pytest.fixture
def registry():
    return CircuitBreakerRegistry()


async def test_starts_closed(registry):
    assert await registry.is_available("groq") is True
    assert await registry.get_state("groq") == "closed"


async def test_stays_closed_below_threshold(registry):
    for _ in range(FAILURE_THRESHOLD - 1):
        await registry.record_failure("groq")
    assert await registry.get_state("groq") == "closed"
    assert await registry.is_available("groq") is True


async def test_opens_at_threshold(registry):
    for _ in range(FAILURE_THRESHOLD):
        await registry.record_failure("groq")
    assert await registry.get_state("groq") == "open"
    assert await registry.is_available("groq") is False


async def test_success_resets_failure_count(registry):
    await registry.record_failure("groq")
    await registry.record_failure("groq")
    await registry.record_success("groq")
    for _ in range(FAILURE_THRESHOLD - 1):
        await registry.record_failure("groq")
    # If the count hadn't reset, this would already be open.
    assert await registry.get_state("groq") == "closed"


async def test_half_open_after_reset_timeout(registry, monkeypatch):
    for _ in range(FAILURE_THRESHOLD):
        await registry.record_failure("groq")
    assert await registry.is_available("groq") is False

    # Simulate the reset timeout having elapsed.
    real_time = time.time()
    monkeypatch.setattr(time, "time", lambda: real_time + RESET_TIMEOUT_SEC + 1)
    assert await registry.is_available("groq") is True
    assert await registry.get_state("groq") == "half_open"


async def test_recovery_from_half_open_closes_circuit(registry, monkeypatch):
    for _ in range(FAILURE_THRESHOLD):
        await registry.record_failure("groq")

    real_time = time.time()
    monkeypatch.setattr(time, "time", lambda: real_time + RESET_TIMEOUT_SEC + 1)
    assert await registry.is_available("groq") is True  # transitions to half_open

    await registry.record_success("groq")
    assert await registry.get_state("groq") == "closed"


async def test_webhook_fires_only_on_open_and_recovered_transitions(registry, monkeypatch):
    fired = []
    monkeypatch.setattr(registry, "_fire_webhook", lambda payload: fired.append(payload["event"]))

    await registry.record_failure("groq")
    await registry.record_failure("groq")
    assert fired == [], "must not fire before the circuit actually opens"

    await registry.record_failure("groq")  # 3rd failure — opens
    assert fired == ["circuit_breaker.opened"]

    await registry.record_success("groq")  # closes — recovered
    assert fired == ["circuit_breaker.opened", "circuit_breaker.recovered"]

    await registry.record_success("groq")  # already closed — no-op, no duplicate event
    assert fired == ["circuit_breaker.opened", "circuit_breaker.recovered"]


async def test_get_all_states_reports_every_touched_provider(registry):
    await registry.record_failure("groq")
    await registry.reset("gemini")

    states = await registry.get_all_states()
    assert states["groq"]["failure_count"] == 1
    assert states["gemini"]["state"] == "closed"


async def test_redis_unreachable_degrades_to_local_fallback(registry, monkeypatch):
    """If Redis raises on every call, the circuit must still function via
    the in-memory fallback rather than blocking provider calls."""
    monkeypatch.setattr("app.services.circuit_breaker.get_redis", lambda: _FailingRedis())

    for _ in range(FAILURE_THRESHOLD):
        await registry.record_failure("groq")
    assert await registry.get_state("groq") == "open"
    assert await registry.is_available("groq") is False


class _FailingRedis:
    """Minimal stand-in whose every method raises, simulating Redis being down."""
    def __getattr__(self, _name):
        async def _raise(*_a, **_k):
            import redis.exceptions
            raise redis.exceptions.ConnectionError("simulated redis outage")
        return _raise
