import uuid
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatRequest, ChatResponse, UsageInfo, MetaInfo
from app.services.providers import call_groq, call_gemini
from app.services.cache import check_cache
from app.services.circuit_breaker import registry as cb          # ← new
from app.db.database import get_db
from app.config import settings

# Import Celery task (fire-and-forget post-processing)
from app.tasks import post_process_task

router = APIRouter()


def _truncate(s: str | None, n: int = 280) -> str | None:
    if not s:
        return None
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _prompt_preview(messages: list[dict]) -> str | None:
    # Prefer the last user message (what engineers usually want to inspect).
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return _truncate(m.get("content"))
    # Fallback: last message content.
    if messages and isinstance(messages[-1].get("content"), str):
        return _truncate(messages[-1].get("content"))
    return None 


def verify_api_key(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth format")
    token = authorization.replace("Bearer ", "")
    if token != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


async def _try_provider(name: str, fn) -> dict | None:
    """
    Attempt a provider call with circuit breaker protection.
    Returns result dict on success, None on any failure.
    """
    if not cb.is_available(name):
        print(f"[CircuitBreaker] {name} is OPEN — skipping immediately")
        return None

    try:
        result = await fn()
        cb.record_success(name)
        return result

    except httpx.TimeoutException:
        print(f"[CircuitBreaker] {name} TIMEOUT — recording failure")
        cb.record_failure(name)
        return None

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        print(f"[CircuitBreaker] {name} returned HTTP {status_code} — recording failure")
        cb.record_failure(name)   # ← record ALL HTTP errors now, not just some
        return None               # ← always return None, never re-raise

    except Exception as e:
        print(f"[CircuitBreaker] {name} unexpected error: {e}")
        cb.record_failure(name)
        return None


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    messages    = [m.model_dump() for m in request.messages]
    request_id  = str(uuid.uuid4())
    wall_start  = time.monotonic()
    stage: dict[str, int] = {}
    prompt_prev = _prompt_preview(messages)
    fallback_from: str | None = None
    status: str = "success"
    error_type: str | None = None

    # ── 1. Semantic cache check ───────────────────────────────────────
    if not request.bypass_cache:
        t_cache = time.monotonic()
        cached = await check_cache(db=db, messages=messages)
        stage["cache_check_ms"] = int((time.monotonic() - t_cache) * 1000)

        if cached:
            wall_latency = int((time.monotonic() - wall_start) * 1000)

            # Fire background task — don't await DB/cache writes.
            task_start = time.monotonic()
            post_process_task.delay(
                request_id=request_id,
                provider=cached["provider"],
                model=cached["model"],
                input_tokens=cached["input_tokens"],
                output_tokens=cached["output_tokens"],
                cost_usd=0.0,
                latency_ms=wall_latency,
                cache_hit=True,
                status="success",
                error_type=None,
                fallback_from=None,
                messages=messages,
                response=None,  # no response to cache — already cached
                prompt_preview=prompt_prev,
                response_preview=_truncate(cached.get("content")),
            )
            task_queue_ms = int((time.monotonic() - task_start) * 1000)
            print(f"[Async] Task queued in {task_queue_ms}ms")

            if settings.log_stage_timings:
                total_ms = int((time.monotonic() - wall_start) * 1000)
                print(
                    "[Timing] "
                    f"id={request_id} cache_hit=true total_ms={total_ms} "
                    f"cache_check_ms={stage.get('cache_check_ms')} "
                    f"task_queue_ms={task_queue_ms}"
                )

            return ChatResponse(
                id=request_id, content=cached["content"],
                provider=cached["provider"], model=cached["model"],
                usage=UsageInfo(input_tokens=cached["input_tokens"],
                                output_tokens=cached["output_tokens"],
                                cost_usd=0.0),
                meta=MetaInfo(cache_hit=True, latency_ms=wall_latency,
                              provider=cached["provider"], fallback=None),
            )

    # ── 2. Try Groq (with circuit breaker) ───────────────────────────
    result = None

    t_groq = time.monotonic()
    result = await _try_provider(
        "groq",
        lambda: call_groq(
            messages=messages, model=request.model,
            max_tokens=request.max_tokens, temperature=request.temperature,
        )
    )
    stage["groq_ms"] = int((time.monotonic() - t_groq) * 1000)

    if result:
        status = "success"

    # ── 3. Try Gemini if Groq failed or was open ──────────────────────
    if result is None:
        fallback_from = "groq"
        t_gemini = time.monotonic()
        result = await _try_provider(
            "gemini",
            lambda: call_gemini(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        )
        stage["gemini_ms"] = int((time.monotonic() - t_gemini) * 1000)
        if result:
            status = "fallback"

    # ── 4. All providers failed ───────────────────────────────────────
    if result is None:
        task_start = time.monotonic()
        post_process_task.delay(
            request_id=request_id,
            provider="none",
            model=request.model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=int((time.monotonic() - wall_start) * 1000),
            cache_hit=False,
            status="error",
            error_type="all_providers_failed",
            fallback_from=fallback_from,
            messages=messages,
            response=None,
            prompt_preview=prompt_prev,
            response_preview=None,
        )
        task_queue_ms = int((time.monotonic() - task_start) * 1000)
        print(f"[Async] Task queued in {task_queue_ms}ms")

        if settings.log_stage_timings:
            total_ms = int((time.monotonic() - wall_start) * 1000)
            print(
                "[Timing] "
                f"id={request_id} cache_hit=false status=error total_ms={total_ms} "
                f"cache_check_ms={stage.get('cache_check_ms')} groq_ms={stage.get('groq_ms')} "
                f"gemini_ms={stage.get('gemini_ms')} task_queue_ms={task_queue_ms}"
            )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "All providers unavailable",
                "circuit_states": cb.get_all_states(),
            }
        )

    wall_latency = int((time.monotonic() - wall_start) * 1000)

    # ── 5. Return response FIRST ─────────────────────────────────────
    response_obj = ChatResponse(
        id=request_id, content=result["content"],
        provider=result["provider"], model=result["model"],
        usage=UsageInfo(input_tokens=result["input_tokens"],
                        output_tokens=result["output_tokens"],
                        cost_usd=result["cost_usd"]),
        meta=MetaInfo(cache_hit=False, latency_ms=wall_latency,
                      provider=result["provider"], fallback=fallback_from),
    )

    # ── 6. Fire background task AFTER building response ──────────────
    task_start = time.monotonic()
    post_process_task.delay(
        request_id=request_id,
        provider=result["provider"],
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=result["cost_usd"],
        latency_ms=wall_latency,
        cache_hit=False,
        status=status,
        error_type=error_type,
        fallback_from=fallback_from,
        messages=messages,
        response=result,
        prompt_preview=prompt_prev,
        response_preview=_truncate(result.get("content")),
    )
    task_queue_ms = int((time.monotonic() - task_start) * 1000)
    print(f"[Async] Task queued in {task_queue_ms}ms")

    if settings.log_stage_timings:
        total_ms = int((time.monotonic() - wall_start) * 1000)
        print(
            "[Timing] "
            f"id={request_id} cache_hit=false status={status} total_ms={total_ms} "
            f"cache_check_ms={stage.get('cache_check_ms')} groq_ms={stage.get('groq_ms')} "
            f"gemini_ms={stage.get('gemini_ms')} task_queue_ms={task_queue_ms}"
        )

    return response_obj