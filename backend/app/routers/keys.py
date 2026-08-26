# API key management — create, list, revoke, and rotate per-tenant keys.
# Every endpoint requires the master admin key (verify_master_key).
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.routers.gateway import verify_master_key
from app.services import api_keys as api_keys_service
from app.services.rate_limiter import limiter

router = APIRouter(prefix="/v1/keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human label, e.g. 'production'")
    rate_limit: int = Field(
        default_factory=lambda: settings.default_rate_limit_per_minute,
        ge=1, le=100_000,
        description="Requests per minute",
    )


class CreateKeyResponse(BaseModel):
    id: str
    key: str            # raw key — shown exactly once, never retrievable again
    key_prefix: str
    name: str
    rate_limit: int


class KeyInfo(BaseModel):
    id: str
    key_prefix: str
    name: str
    is_active: bool
    rate_limit: int
    current_usage: int   # requests consumed in the current 60s window
    created_at: str
    last_used: str | None


@router.post("", response_model=CreateKeyResponse)
async def create_key(
    body: CreateKeyRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_master_key),
):
    """Create a new per-tenant API key. The raw key is returned exactly once — store it now."""
    row, raw_key = await api_keys_service.create_key(db=db, name=body.name, rate_limit=body.rate_limit)
    return CreateKeyResponse(
        id=row.id, key=raw_key, key_prefix=row.key_prefix,
        name=row.name, rate_limit=row.rate_limit,
    )


@router.get("", response_model=list[KeyInfo])
async def list_keys(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_master_key),
):
    """List every API key with its rate-limit config and current usage. Raw key values are never returned."""
    rows = await api_keys_service.list_keys(db=db)
    out: list[KeyInfo] = []
    for row in rows:
        usage = await limiter.current_usage(key_hash=row.key_hash)
        out.append(KeyInfo(
            id=row.id,
            key_prefix=row.key_prefix,
            name=row.name,
            is_active=row.is_active,
            rate_limit=row.rate_limit,
            current_usage=usage,
            created_at=row.created_at.isoformat() if row.created_at else "",
            last_used=row.last_used.isoformat() if row.last_used else None,
        ))
    return out


@router.delete("/{key_id}")
async def delete_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_master_key),
):
    """Soft-delete a key — sets is_active=False and evicts it from the auth cache. The row is kept for audit history."""
    row = await api_keys_service.revoke_key(db=db, key_id=key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": f"Key {row.key_prefix}... deactivated"}


@router.post("/{key_id}/rotate", response_model=CreateKeyResponse)
async def rotate_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_master_key),
):
    """Issue a new raw key for the same key id and invalidate the old one immediately."""
    result = await api_keys_service.rotate_key(db=db, key_id=key_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Key not found")
    row, raw_key = result
    return CreateKeyResponse(
        id=row.id, key=raw_key, key_prefix=row.key_prefix,
        name=row.name, rate_limit=row.rate_limit,
    )
