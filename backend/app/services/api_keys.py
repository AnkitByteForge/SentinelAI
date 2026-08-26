# Per-tenant API key generation, hashing, lookup, and Redis-cached resolution.
from __future__ import annotations

import hashlib
import json
import secrets

import redis.exceptions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ApiKey
from app.services.redis_client import get_redis

# Raw key format: "sk-sent-" + 32 hex chars, e.g. sk-sent-a3f8c2e1b9d4...
KEY_PREFIX = "sk-sent-"
KEY_RANDOM_HEX_CHARS = 32
DISPLAY_PREFIX_HEX_CHARS = 8  # shown in listings, e.g. "sk-sent-a3f8c2e1"


def _generate_raw_key() -> str:
    return KEY_PREFIX + secrets.token_hex(KEY_RANDOM_HEX_CHARS // 2)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _display_prefix(raw_key: str) -> str:
    return raw_key[: len(KEY_PREFIX) + DISPLAY_PREFIX_HEX_CHARS]


def _cache_key(key_hash: str) -> str:
    return f"apikey:{key_hash}"


async def create_key(db: AsyncSession, name: str, rate_limit: int) -> tuple[ApiKey, str]:
    """Create a new API key. Returns (row, raw_key) — raw_key is only ever available here."""
    raw_key = _generate_raw_key()
    row = ApiKey(
        key_hash=hash_key(raw_key),
        key_prefix=_display_prefix(raw_key),
        name=name,
        is_active=True,
        rate_limit=rate_limit,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw_key


async def list_keys(db: AsyncSession) -> list[ApiKey]:
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return list(result.scalars().all())


async def revoke_key(db: AsyncSession, key_id: str) -> ApiKey | None:
    """Soft delete — flips is_active to False and evicts any cached lookup."""
    row = await db.get(ApiKey, key_id)
    if row is None:
        return None
    row.is_active = False
    await db.commit()
    await db.refresh(row)
    await _invalidate_cache(row.key_hash)
    return row


async def rotate_key(db: AsyncSession, key_id: str) -> tuple[ApiKey, str] | None:
    """Issue a new raw key for the same row id and invalidate the old hash immediately."""
    row = await db.get(ApiKey, key_id)
    if row is None:
        return None
    old_hash = row.key_hash
    raw_key = _generate_raw_key()
    row.key_hash = hash_key(raw_key)
    row.key_prefix = _display_prefix(raw_key)
    row.is_active = True
    await db.commit()
    await db.refresh(row)
    await _invalidate_cache(old_hash)
    return row, raw_key


async def _invalidate_cache(key_hash: str) -> None:
    try:
        r = get_redis()
        await r.delete(_cache_key(key_hash))
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        pass  # Redis down — the 60s TTL will still expire the stale entry.


async def resolve_key(db: AsyncSession, raw_key: str) -> dict | None:
    """
    Resolve a raw bearer token to its key record.

    Checks the Redis cache first (60s TTL, key "apikey:{hash}"); falls back
    to Postgres on a cache miss or if Redis is unreachable. Returns None if
    the key doesn't exist or has been deactivated — inactive lookups are
    cached too, so repeated use of a revoked key doesn't keep hitting Postgres.
    """
    key_hash = hash_key(raw_key)
    cache_key = _cache_key(key_hash)

    try:
        r = get_redis()
        cached = await r.get(cache_key)
        if cached is not None:
            data = json.loads(cached)
            return data if data.get("is_active") else None
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        r = None  # fall through to Postgres; skip caching the result below too

    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    row = result.scalar_one_or_none()
    if row is None:
        return None

    data = {
        "id": row.id,
        "name": row.name,
        "rate_limit": row.rate_limit,
        "is_active": row.is_active,
        "key_hash": row.key_hash,
    }

    try:
        r = get_redis()
        await r.set(cache_key, json.dumps(data), ex=settings.api_key_cache_ttl_seconds)
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        pass

    return data if row.is_active else None
