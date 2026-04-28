# POST /v1/chat endpoint


import uuid
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatRequest, ChatResponse, UsageInfo, MetaInfo
from app.services.providers import call_groq, call_gemini
from app.services.cache import check_cache, store_in_cache     # ← new
from app.db.database import get_db
from app.db.models import RequestLog
from app.config import settings

router = APIRouter()


def verify_api_key(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth format")
    token = authorization.replace("Bearer ", "")
    if token != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    messages       = [m.model_dump() for m in request.messages]
    request_id     = str(uuid.uuid4())
    wall_start     = time.monotonic()
    fallback_from  = None
    status         = "success"
    error_type     = None

    # ── 1. Semantic cache check (skip if bypass_cache=True) ──────────
    if not request.bypass_cache:
        cached = await check_cache(db=db, messages=messages)

        if cached:
            wall_latency = int((time.monotonic() - wall_start) * 1000)

            # Log the cache hit to requests table
            log = RequestLog(
                id            = request_id,
                provider      = cached["provider"],
                model         = cached["model"],
                input_tokens  = cached["input_tokens"],
                output_tokens = cached["output_tokens"],
                cost_usd      = 0.0,       # cache hits are free
                latency_ms    = wall_latency,
                cache_hit     = True,
                status        = "success",
            )
            db.add(log)
            await db.commit()

            return ChatResponse(
                id       = request_id,
                content  = cached["content"],
                provider = cached["provider"],
                model    = cached["model"],
                usage    = UsageInfo(
                    input_tokens  = cached["input_tokens"],
                    output_tokens = cached["output_tokens"],
                    cost_usd      = 0.0,
                ),
                meta = MetaInfo(
                    cache_hit  = True,
                    latency_ms = wall_latency,
                    provider   = cached["provider"],
                    fallback   = None,
                ),
            )

    # ── 2. Cache miss → call LLM ─────────────────────────────────────
    result = None

    try:
        result = await call_groq(
            messages    = messages,
            model       = request.model,
            max_tokens  = request.max_tokens,
            temperature = request.temperature,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (429, 500, 502, 503):
            fallback_from = "groq"
            status        = "fallback"
        else:
            status     = "error"
            error_type = f"http_{e.response.status_code}"
    except httpx.TimeoutException:
        fallback_from = "groq"
        status        = "fallback"
        error_type    = "timeout"

    # ── 3. Fallback to Gemini ─────────────────────────────────────────
    if fallback_from == "groq":
        try:
            result = await call_gemini(
                messages    = messages,
                max_tokens  = request.max_tokens,
                temperature = request.temperature,
            )
            status = "fallback"
        except Exception:
            status     = "error"
            error_type = "all_providers_failed"

    if result is None:
        log = RequestLog(
            id=request_id, provider="none", model=request.model,
            status="error", error_type=error_type or "unknown",
        )
        db.add(log)
        await db.commit()
        raise HTTPException(status_code=503, detail="All providers unavailable")

    wall_latency = int((time.monotonic() - wall_start) * 1000)

    # ── 4. Store in cache for future requests ─────────────────────────
    await store_in_cache(db=db, messages=messages, response=result)

    # ── 5. Log to requests table ──────────────────────────────────────
    log = RequestLog(
        id            = request_id,
        provider      = result["provider"],
        model         = result["model"],
        input_tokens  = result["input_tokens"],
        output_tokens = result["output_tokens"],
        cost_usd      = result["cost_usd"],
        latency_ms    = wall_latency,
        cache_hit     = False,
        status        = status,
        error_type    = error_type,
        fallback_from = fallback_from,
    )
    db.add(log)
    await db.commit()

    return ChatResponse(
        id       = request_id,
        content  = result["content"],
        provider = result["provider"],
        model    = result["model"],
        usage    = UsageInfo(
            input_tokens  = result["input_tokens"],
            output_tokens = result["output_tokens"],
            cost_usd      = result["cost_usd"],
        ),
        meta = MetaInfo(
            cache_hit  = False,
            latency_ms = wall_latency,
            provider   = result["provider"],
            fallback   = fallback_from,
        ),
    )