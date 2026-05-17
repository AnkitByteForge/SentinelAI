import asyncio
import uuid
from app.worker import celery_app


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Task 1: Log request to DB ────────────────────────────────────────
@celery_app.task(name="log_request", max_retries=3, default_retry_delay=5)
def log_request_task(
    request_id:    str,
    provider:      str,
    model:         str,
    input_tokens:  int,
    output_tokens: int,
    cost_usd:      float,
    latency_ms:    int,
    cache_hit:     bool,
    status:        str,
    error_type:    str | None,
    fallback_from: str | None,
    prompt_preview: str | None = None,
    response_preview: str | None = None,
):
    """Write request log to DB. Runs in background after response is returned."""
    async def _write():
        from app.db.database import AsyncSessionLocal
        from app.db.models import RequestLog

        async with AsyncSessionLocal() as db:
            log = RequestLog(
                id            = request_id,
                provider      = provider,
                model         = model,
                input_tokens  = input_tokens,
                output_tokens = output_tokens,
                cost_usd      = cost_usd,
                latency_ms    = latency_ms,
                cache_hit     = cache_hit,
                status        = status,
                error_type    = error_type,
                fallback_from = fallback_from,
                prompt_preview = prompt_preview,
                response_preview = response_preview,
            )
            db.add(log)
            await db.commit()

    _run_async(_write())


# ── Task 2: Store response in semantic cache ─────────────────────────
@celery_app.task(name="store_cache", max_retries=3, default_retry_delay=5)
def store_cache_task(messages: list[dict], response: dict):
    """Store LLM response in semantic cache. Runs after response is returned."""
    async def _store():
        from app.db.database import AsyncSessionLocal
        from app.services.cache import store_in_cache

        async with AsyncSessionLocal() as db:
            await store_in_cache(db=db, messages=messages, response=response)

    _run_async(_store())


# ── Task 3: Combined fire-and-forget (single task call) ──────────────
@celery_app.task(name="post_process", max_retries=3, default_retry_delay=5)
def post_process_task(
    request_id:    str,
    provider:      str,
    model:         str,
    input_tokens:  int,
    output_tokens: int,
    cost_usd:      float,
    latency_ms:    int,
    cache_hit:     bool,
    status:        str,
    error_type:    str | None,
    fallback_from: str | None,
    messages:      list[dict],
    response:      dict | None,  # None for cache hits
    prompt_preview: str | None = None,
    response_preview: str | None = None,
):
    """
    Single combined task: log to DB + store in cache.
    One task call instead of two — reduces Redis round trips.
    """
    async def _run():
        from app.db.database import AsyncSessionLocal
        from app.db.models import RequestLog
        from app.services.cache import store_in_cache

        async with AsyncSessionLocal() as db:
            # Write request log
            log = RequestLog(
                id            = request_id,
                provider      = provider,
                model         = model,
                input_tokens  = input_tokens,
                output_tokens = output_tokens,
                cost_usd      = cost_usd,
                latency_ms    = latency_ms,
                cache_hit     = cache_hit,
                status        = status,
                error_type    = error_type,
                fallback_from = fallback_from,
                prompt_preview = prompt_preview,
                response_preview = response_preview,
            )
            db.add(log)
            await db.commit()

            # Store in cache only for LLM responses (not cache hits)
            if response and not cache_hit:
                await store_in_cache(db=db, messages=messages, response=response)

    _run_async(_run())