# Per-provider circuit breaker, backed by Redis.
#
# A single in-memory dict (the original implementation) only produces
# correct circuit state for one process — with more than one backend
# replica, each would independently decide a provider is healthy or
# down, defeating the point of a shared circuit. State now lives in
# Redis (one hash per provider) and falls back to a local in-memory copy
# if Redis is unreachable, so a Redis outage degrades circuit-breaker
# accuracy but never blocks provider calls — the same philosophy already
# used by services/rate_limiter.py and services/api_keys.py.
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict

import redis.exceptions

from app.services.redis_client import get_redis

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3      # open after 3 consecutive failures
RESET_TIMEOUT_SEC = 60.0   # try again after 60 seconds
_KNOWN_PROVIDERS_KEY = "circuit:known_providers"


class CircuitState(str, Enum):
    CLOSED    = "closed"      # healthy — requests go through
    OPEN      = "open"        # broken  — requests blocked instantly
    HALF_OPEN = "half_open"   # testing — one request allowed through


@dataclass
class ProviderCircuit:
    state:             CircuitState = CircuitState.CLOSED
    failure_count:     int          = 0
    last_failure_time: float        = 0.0
    opened_at:         float        = 0.0


def _key(provider: str) -> str:
    return f"circuit:{provider}"


def _parse(data: dict) -> ProviderCircuit:
    if not data:
        return ProviderCircuit()
    return ProviderCircuit(
        state=CircuitState(data.get("state", "closed")),
        failure_count=int(data.get("failure_count", 0)),
        last_failure_time=float(data.get("last_failure_time", 0.0)),
        opened_at=float(data.get("opened_at", 0.0)),
    )


class CircuitBreakerRegistry:
    """
    Redis-backed circuit state, one hash per provider. All timestamps are
    wall-clock (time.time()), not time.monotonic() — monotonic clocks
    aren't comparable across processes, which matters once state is
    shared over Redis instead of held in one process's memory.
    """

    def __init__(self) -> None:
        # Same-process safety net for when Redis is unreachable — not the
        # source of truth, and not shared across replicas.
        self._local_fallback: Dict[str, ProviderCircuit] = {}

    async def _read(self, provider: str) -> ProviderCircuit:
        try:
            r = get_redis()
            data = await r.hgetall(_key(provider))
            return _parse(data)
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            return self._local_fallback.get(provider, ProviderCircuit())

    async def _persist(self, provider: str, circuit: ProviderCircuit) -> None:
        self._local_fallback[provider] = circuit
        try:
            r = get_redis()
            await r.hset(_key(provider), mapping={
                "state":              circuit.state.value,
                "failure_count":      circuit.failure_count,
                "last_failure_time":  circuit.last_failure_time,
                "opened_at":          circuit.opened_at,
            })
            await r.sadd(_KNOWN_PROVIDERS_KEY, provider)
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            pass  # local fallback above already updated

    async def is_available(self, provider: str) -> bool:
        """
        Can we send a request to this provider right now?
        CLOSED    → yes
        OPEN      → only if reset timeout has passed (transitions to HALF_OPEN)
        HALF_OPEN → yes (we're testing recovery)
        """
        circuit = await self._read(provider)

        if circuit.state == CircuitState.CLOSED:
            return True

        if circuit.state == CircuitState.OPEN:
            elapsed = time.time() - circuit.opened_at
            if elapsed < RESET_TIMEOUT_SEC:
                return False   # still open, block immediately
            # Timeout passed — allow a test request through. (Under
            # multiple replicas, more than one may flip this at once and
            # each send a single probe — acceptable; nothing here depends
            # on exactly one probe firing system-wide.)
            circuit.state = CircuitState.HALF_OPEN
            await self._persist(provider, circuit)
            return True

        return True  # HALF_OPEN — allow through for testing

    async def record_success(self, provider: str) -> None:
        """Call this when a provider request succeeds."""
        circuit = await self._read(provider)
        was_open = circuit.state != CircuitState.CLOSED
        circuit.state = CircuitState.CLOSED
        circuit.failure_count = 0
        await self._persist(provider, circuit)

        if was_open:
            await self._notify_recovered(provider)

    async def record_failure(self, provider: str) -> None:
        """
        Call this when a provider request fails. Increments failure_count
        with Redis HINCRBY — atomic, so the count stays correct even
        under concurrent requests across replicas, unlike a
        read-modify-write. Opens the circuit if the threshold is reached.
        """
        now = time.time()
        try:
            r = get_redis()
            new_count = await r.hincrby(_key(provider), "failure_count", 1)
            await r.hset(_key(provider), "last_failure_time", now)
            await r.sadd(_KNOWN_PROVIDERS_KEY, provider)
            current_state = await r.hget(_key(provider), "state") or CircuitState.CLOSED.value
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            circuit = self._local_fallback.get(provider, ProviderCircuit())
            circuit.failure_count += 1
            circuit.last_failure_time = now
            self._local_fallback[provider] = circuit
            new_count = circuit.failure_count
            current_state = circuit.state.value

        should_open = new_count >= FAILURE_THRESHOLD and current_state != CircuitState.OPEN.value
        if not should_open:
            return

        opened_by_us = await self._claim_open(provider, now)
        if opened_by_us:
            logger.warning(
                "circuit breaker opened for %s after %d consecutive failures",
                provider, new_count,
            )
            await self._notify_opened(provider, new_count)

    async def _claim_open(self, provider: str, now: float) -> bool:
        """
        Transitions the circuit to OPEN. Uses a short-lived Redis lock (SET
        NX) so that if several replicas hit the failure threshold at once,
        only one of them fires the "opened" webhook — duplicate
        notifications are noise, not correctness bugs, but cheap to avoid.
        """
        try:
            r = get_redis()
            claimed = await r.set(f"{_key(provider)}:opening_lock", "1", nx=True, ex=5)
            await r.hset(_key(provider), mapping={
                "state": CircuitState.OPEN.value,
                "opened_at": now,
            })
            return bool(claimed)
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            circuit = self._local_fallback.get(provider, ProviderCircuit())
            circuit.state = CircuitState.OPEN
            circuit.opened_at = now
            self._local_fallback[provider] = circuit
            return True

    async def _notify_opened(self, provider: str, failure_count: int) -> None:
        from datetime import datetime, timezone

        self._fire_webhook({
            "event":         "circuit_breaker.opened",
            "provider":      provider,
            "failure_count": failure_count,
            "opened_at":     datetime.now(timezone.utc).isoformat(),
            "message":       f"{provider} circuit opened after "
                              f"{failure_count} consecutive failures",
        })

    async def _notify_recovered(self, provider: str) -> None:
        from datetime import datetime, timezone

        self._fire_webhook({
            "event":        "circuit_breaker.recovered",
            "provider":     provider,
            "recovered_at": datetime.now(timezone.utc).isoformat(),
            "message":      f"{provider} circuit closed — provider recovered",
        })

    def _fire_webhook(self, payload: dict) -> None:
        """
        Queue webhook delivery via Celery — never block or raise from here.
        Lazy imports avoid a module-load-time dependency between the circuit
        breaker (a low-level service) and Celery/tasks.
        """
        try:
            from app.config import settings
            if not settings.webhook_url:
                return
            from app.tasks import send_webhook_task
            send_webhook_task.delay(payload)
        except Exception as e:
            logger.error("failed to queue circuit breaker webhook: %s", e)

    async def get_state(self, provider: str) -> str:
        """Returns current state string for API responses."""
        circuit = await self._read(provider)
        return circuit.state.value

    async def get_all_states(self) -> Dict[str, dict]:
        """Returns state of all tracked providers — for /health and /v1/circuit/states."""
        providers: set[str] = set(self._local_fallback.keys())
        try:
            r = get_redis()
            providers |= set(await r.smembers(_KNOWN_PROVIDERS_KEY))
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            pass

        result: Dict[str, dict] = {}
        for provider in providers:
            circuit = await self._read(provider)
            result[provider] = {
                "state":          circuit.state.value,
                "failure_count":  circuit.failure_count,
                "last_failure":   circuit.last_failure_time,
            }
        return result

    async def reset(self, provider: str) -> None:
        """Manually reset a circuit — for admin/testing use."""
        await self._persist(provider, ProviderCircuit())
        try:
            r = get_redis()
            await r.delete(f"{_key(provider)}:opening_lock")
        except (redis.exceptions.RedisError, ConnectionError, OSError):
            pass


# ── Single global instance — imported wherever needed ────────────────
registry = CircuitBreakerRegistry()
