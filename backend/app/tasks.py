import asyncio
import logging

from app.worker import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        # Every call gets a brand-new event loop, but app.db.database's
        # async engine is a single pooled-connection singleton shared
        # across every call in this process. A connection checked back
        # into the pool when this loop closes would get reused under a
        # DIFFERENT loop on the next task — asyncpg forbids that
        # ("Task ... got Future ... attached to a different loop"),
        # intermittently depending on pool churn. Same bug class already
        # fixed for tests via conftest.py's _dispose_engine_between_tests;
        # disposing here forces the next task to open fresh connections
        # instead of reusing one bound to this closing loop.
        from app.db.database import engine
        loop.run_until_complete(engine.dispose())
        loop.close()


# ── Task logic, as plain async functions ──────────────────────────────
# Kept separate from the @celery_app.task wrappers below so tests can
# await them directly on their own event loop instead of going through
# _run_async's new-event-loop-per-call (which only makes sense for a real
# Celery worker process, not for reusing the same DB engine a test's own
# event loop is already using — asyncpg connections aren't safe to share
# across event loop instances).

async def _log_request(
    request_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    cache_hit: bool,
    status: str,
    error_type: str | None,
    fallback_from: str | None,
    prompt_preview: str | None = None,
    response_preview: str | None = None,
):
    from app.db.database import AsyncSessionLocal
    from app.db.models import RequestLog

    async with AsyncSessionLocal() as db:
        log = RequestLog(
            id=request_id, provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, latency_ms=latency_ms, cache_hit=cache_hit,
            status=status, error_type=error_type, fallback_from=fallback_from,
            prompt_preview=prompt_preview, response_preview=response_preview,
        )
        db.add(log)
        await db.commit()


async def _post_process(
    request_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    cache_hit: bool,
    status: str,
    error_type: str | None,
    fallback_from: str | None,
    messages: list[dict],
    response: dict | None,  # None for cache hits
    prompt_preview: str | None = None,
    response_preview: str | None = None,
):
    """Log to DB, then store in cache if this was a live LLM response (not a cache hit)."""
    from app.db.database import AsyncSessionLocal
    from app.db.models import RequestLog
    from app.services.cache import store_in_cache

    async with AsyncSessionLocal() as db:
        log = RequestLog(
            id=request_id, provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, latency_ms=latency_ms, cache_hit=cache_hit,
            status=status, error_type=error_type, fallback_from=fallback_from,
            prompt_preview=prompt_preview, response_preview=response_preview,
        )
        db.add(log)
        await db.commit()

        if response and not cache_hit:
            await store_in_cache(db=db, messages=messages, response=response)


async def _touch_api_key(key_hash: str):
    from datetime import datetime, timezone

    from sqlalchemy import update as sql_update

    from app.db.database import AsyncSessionLocal
    from app.db.models import ApiKey

    async with AsyncSessionLocal() as db:
        await db.execute(
            sql_update(ApiKey)
            .where(ApiKey.key_hash == key_hash)
            .values(last_used=datetime.now(timezone.utc))
        )
        await db.commit()


async def _deliver_webhook(payload: dict):
    from app.services.webhook import send_webhook

    await send_webhook(payload)


# ── Celery task wrappers — thin, just bridge sync Celery to the above ──

@celery_app.task(name="log_request", max_retries=3, default_retry_delay=5)
def log_request_task(**kwargs):
    """Write request log to DB. Runs in background after response is returned."""
    _run_async(_log_request(**kwargs))


@celery_app.task(name="store_cache", max_retries=3, default_retry_delay=5)
def store_cache_task(messages: list[dict], response: dict):
    """Store LLM response in semantic cache. Runs after response is returned."""
    async def _store():
        from app.db.database import AsyncSessionLocal
        from app.services.cache import store_in_cache

        async with AsyncSessionLocal() as db:
            await store_in_cache(db=db, messages=messages, response=response)

    _run_async(_store())


@celery_app.task(name="post_process", max_retries=3, default_retry_delay=5)
def post_process_task(**kwargs):
    """
    Single combined task: log to DB + store in cache.
    One task call instead of two — reduces Redis round trips.
    """
    _run_async(_post_process(**kwargs))


@celery_app.task(name="touch_api_key", max_retries=3, default_retry_delay=5)
def touch_api_key_task(key_hash: str):
    """
    Update the last_used timestamp for an API key. Fired from verify_api_key
    after every successfully authenticated request so the hot path never
    waits on this write.
    """
    try:
        _run_async(_touch_api_key(key_hash))
    except Exception as e:
        logger.error("api key last_used update failed: %s", e, extra={"key_hash": key_hash})


@celery_app.task(name="send_webhook", max_retries=3, default_retry_delay=5)
def send_webhook_task(payload: dict):
    """
    Deliver a webhook notification in the background. Used for circuit
    breaker state-change events so a slow or unreachable webhook endpoint
    can never add latency to request processing.
    """
    try:
        _run_async(_deliver_webhook(payload))
    except Exception as e:
        logger.error("webhook delivery task failed: %s", e)
