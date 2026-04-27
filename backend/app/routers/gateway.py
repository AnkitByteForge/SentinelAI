import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatRequest, ChatResponse, UsageInfo, MetaInfo
from app.services.providers import call_groq, call_gemini
from app.db.database import get_db
from app.db.models import RequestLog
from app.config import settings

router = APIRouter()

def verify_api_key(authorization: str = Header(...)):
    """Simple API key auth. Bearer {key} format."""
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
    messages = [m.model_dump() for m in request.messages]
    request_id = str(uuid.uuid4())
    result = None
    fallback_from = None
    status = "success"
    error_type = None

    # ── Try Groq first ──────────────────────────────────────────────
    try:
        result = await call_groq(
            messages=messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (429, 500, 502, 503):
            # Provider is down → try fallback
            fallback_from = "groq"
            status = "fallback"
        else:
            status = "error"
            error_type = f"http_{e.response.status_code}"
    except httpx.TimeoutException:
        fallback_from = "groq"
        status = "fallback"
        error_type = "timeout"

    # ── Fallback to Gemini if Groq failed ───────────────────────────
    if fallback_from == "groq":
        try:
            result = await call_gemini(
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
            status = "fallback"   # mark as fallback, not error
        except Exception as e:
            status = "error"
            error_type = "all_providers_failed"

    # ── Both providers failed ───────────────────────────────────────
    if result is None:
        log = RequestLog(
            id=request_id, provider="none", model=request.model,
            status="error", error_type=error_type or "unknown",
        )
        db.add(log)
        await db.commit()
        raise HTTPException(status_code=503, detail="All providers unavailable")

    # ── Log to DB (async — does not block response) ─────────────────
    log = RequestLog(
        id=request_id,
        provider=result["provider"],
        model=result["model"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=result["cost_usd"],
        latency_ms=result["latency_ms"],
        cache_hit=False,          # we'll add cache in next phase
        status=status,
        error_type=error_type,
        fallback_from=fallback_from,
    )
    db.add(log)
    await db.commit()

    # ── Build and return response ───────────────────────────────────
    return ChatResponse(
        id=request_id,
        content=result["content"],
        provider=result["provider"],
        model=result["model"],
        usage=UsageInfo(
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cost_usd=result["cost_usd"],
        ),
        meta=MetaInfo(
            cache_hit=False,
            latency_ms=result["latency_ms"],
            provider=result["provider"],
            fallback=fallback_from,
        ),
    )