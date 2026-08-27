# Detailed system health checks — database, Redis, Celery, and LLM providers.
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.circuit_breaker import registry as cb
from app.services.redis_client import get_redis


async def _check_database(db: AsyncSession) -> dict:
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            db.execute(text("SELECT COUNT(*) FROM requests")),
            timeout=settings.health_db_timeout_seconds,
        )
        count = result.scalar_one()
        return {
            "status": "healthy",
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "detail": f"postgresql+asyncpg connected, {count} rows in requests",
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "detail": str(e),
        }


async def _check_redis() -> dict:
    start = time.monotonic()
    try:
        r = get_redis()
        await asyncio.wait_for(r.ping(), timeout=settings.health_redis_timeout_seconds)
        queue_depth = await asyncio.wait_for(r.llen("celery"), timeout=settings.health_redis_timeout_seconds)
        return {
            "status": "healthy",
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "detail": f"redis connected, queue_depth: {queue_depth}",
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "detail": str(e),
        }


async def _check_celery() -> dict:
    try:
        r = get_redis()
        queue_depth = await asyncio.wait_for(r.llen("celery"), timeout=settings.health_redis_timeout_seconds)
    except Exception as e:
        return {"status": "unknown", "queue_depth": 0, "detail": f"could not read queue depth: {e}"}

    if queue_depth > settings.celery_queue_depth_degraded_threshold:
        return {
            "status": "degraded",
            "queue_depth": queue_depth,
            "detail": f"queue depth {queue_depth} exceeds threshold ({settings.celery_queue_depth_degraded_threshold})",
        }
    return {"status": "healthy", "queue_depth": queue_depth, "detail": "worker processing normally"}


async def _check_providers() -> dict:
    states = await cb.get_all_states()
    out = {}
    for provider in ("groq", "gemini"):
        s = states.get(provider, {"state": "closed", "failure_count": 0, "last_failure": 0.0})
        if s["state"] == "closed":
            status = "healthy"
        elif s["state"] == "half_open":
            status = "degraded"
        else:
            status = "unhealthy"
        out[provider] = {
            "status": status,
            "circuit_state": s["state"],
            "failure_count": s["failure_count"],
        }
    return out


async def get_system_health(db: AsyncSession) -> dict:
    """
    Runs all checks concurrently and rolls them up into one status.

    - "unhealthy": database or Redis unreachable — the caller should
      respond with HTTP 503, since core functionality (auth cache, rate
      limiting, request logging) cannot work without them.
    - "degraded": core infra is fine but something is impaired — a
      provider circuit is open/half-open, or the Celery queue is backing up.
    - "healthy": every check passed.
    """
    db_check, redis_check, celery_check, provider_checks = await asyncio.gather(
        _check_database(db), _check_redis(), _check_celery(), _check_providers()
    )

    if db_check["status"] == "unhealthy" or redis_check["status"] == "unhealthy":
        overall = "unhealthy"
    elif celery_check["status"] == "degraded" or any(
        p["status"] != "healthy" for p in provider_checks.values()
    ):
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "database": db_check,
            "redis": redis_check,
            "celery": celery_check,
            "providers": provider_checks,
        },
        "circuit_breakers": await cb.get_all_states(),
    }
