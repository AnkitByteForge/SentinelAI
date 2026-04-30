import uuid
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatRequest, ChatResponse, UsageInfo, MetaInfo
from app.services.providers import call_groq, call_gemini
from app.services.cache import check_cache, store_in_cache
from app.services.circuit_breaker import registry as cb          # ← new
from app.db.database import get_db
from app.db.models import RequestLog
from app.config import settings

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
    prompt_prev = _prompt_preview(messages)

    # ── 1. Semantic cache check ───────────────────────────────────────
    if not request.bypass_cache:
        cached = await check_cache(db=db, messages=messages)

        if cached:
            wall_latency = int((time.monotonic() - wall_start) * 1000)
            log = RequestLog(
                id=request_id, provider=cached["provider"],
                model=cached["model"], input_tokens=cached["input_tokens"],
                output_tokens=cached["output_tokens"], cost_usd=0.0,
                latency_ms=wall_latency, cache_hit=True, status="success",
                prompt_preview=prompt_prev,
                response_preview=_truncate(cached.get("content")),
            )
            db.add(log)
            await db.commit()

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
    result       = None
    used_provider= None
    fallback_from= None

    result = await _try_provider(
        "groq",
        lambda: call_groq(
            messages=messages, model=request.model,
            max_tokens=request.max_tokens, temperature=request.temperature,
        )
    )

    if result:
        used_provider = "groq"

    # ── 3. Try Gemini if Groq failed or was open ──────────────────────
    if result is None:
        fallback_from = "groq"
        result = await _try_provider(
            "gemini",
            lambda: call_gemini(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        )
        if result:
            used_provider = "gemini"

    # ── 4. All providers failed ───────────────────────────────────────
    if result is None:
        log = RequestLog(
            id=request_id, provider="none", model=request.model,
            status="error", error_type="all_providers_failed",
            prompt_preview=prompt_prev,
            response_preview=None,
        )
        db.add(log)
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "All providers unavailable",
                "circuit_states": cb.get_all_states(),
            }
        )

    wall_latency = int((time.monotonic() - wall_start) * 1000)
    status       = "fallback" if fallback_from else "success"

    # ── 5. Store in cache ─────────────────────────────────────────────
    await store_in_cache(db=db, messages=messages, response=result)

    # ── 6. Log request ────────────────────────────────────────────────
    log = RequestLog(
        id=request_id, provider=result["provider"], model=result["model"],
        input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
        cost_usd=result["cost_usd"], latency_ms=wall_latency,
        cache_hit=False, status=status,
        fallback_from=fallback_from,
        prompt_preview=prompt_prev,
        response_preview=_truncate(result.get("content")),
    )
    db.add(log)
    await db.commit()

    return ChatResponse(
        id=request_id, content=result["content"],
        provider=result["provider"], model=result["model"],
        usage=UsageInfo(input_tokens=result["input_tokens"],
                        output_tokens=result["output_tokens"],
                        cost_usd=result["cost_usd"]),
        meta=MetaInfo(cache_hit=False, latency_ms=wall_latency,
                      provider=result["provider"], fallback=fallback_from),
    )