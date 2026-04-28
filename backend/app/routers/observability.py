from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.db.database import get_db
from app.models import LogsResponse, MetricsResponse
from app.services.queries import get_logs, get_metrics
from app.routers.gateway import verify_api_key   # reuse the same auth
from app.services.cache import get_cache_stats

from app.services.cost import get_pricing_table

router = APIRouter()


@router.get("/v1/logs", response_model=LogsResponse)
async def logs(
    page:     int            = Query(default=1,  ge=1),
    limit:    int            = Query(default=50, ge=1, le=200),
    status:   Optional[str] = Query(default=None, description="success | error | fallback"),
    provider: Optional[str] = Query(default=None, description="groq | gemini"),
    db:       AsyncSession   = Depends(get_db),
    _:        str            = Depends(verify_api_key),
):
    result = await get_logs(db=db, page=page, limit=limit, status=status, provider=provider)
    return LogsResponse(**result)


@router.get("/v1/metrics", response_model=MetricsResponse)
async def metrics(
    window: str          = Query(default="24h", description="1h | 6h | 24h | 7d"),
    db:     AsyncSession = Depends(get_db),
    _:      str          = Depends(verify_api_key),
):
    result = await get_metrics(db=db, window=window)
    return MetricsResponse(**result)


@router.get("/v1/pricing")
async def pricing(_: str = Depends(verify_api_key)):
    """Returns current provider pricing per 1M tokens."""
    return {
        "unit": "USD per 1M tokens",
        "note": "Cost is calculated on every request using real token counts × these rates",
        "providers": get_pricing_table()
    }


@router.get("/v1/cache/stats")
async def cache_stats(
    db: AsyncSession = Depends(get_db),
    _:  str          = Depends(verify_api_key),
):
    return await get_cache_stats(db=db)


@router.delete("/v1/cache/invalidate")
async def invalidate_cache(
    db: AsyncSession = Depends(get_db),
    _:  str          = Depends(verify_api_key),
):
    """Mark all cache entries as stale. Useful for testing."""
    from sqlalchemy import update as sql_update
    from app.db.models import CacheEntry
    await db.execute(sql_update(CacheEntry).values(is_stale=True))
    await db.commit()
    return {"message": "All cache entries invalidated"}