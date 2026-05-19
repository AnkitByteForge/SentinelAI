import hashlib
import uuid
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text
from sentence_transformers import SentenceTransformer

from app.db.models import CacheEntry
from app.services.cost import calculate_cost

# ── Embedding model — loaded once at import time ──────────────────────
_model = SentenceTransformer("all-MiniLM-L6-v2")

SIMILARITY_THRESHOLD = 0.92   # cosine similarity cutoff


def warmup_embedding_model() -> bool:
    """Keep the warmup hook used by startup; model is already loaded."""
    try:
        _model.encode("warmup", normalize_embeddings=True)
        return True
    except Exception:
        return False


def _embed(text_input: str) -> list[float]:
    """Generate normalised 384-dim embedding."""
    vec = _model.encode(text_input, normalize_embeddings=True)
    return vec.tolist()


def _prompt_hash(text_input: str) -> str:
    return hashlib.sha256(text_input.strip().encode()).hexdigest()


def _messages_to_text(messages: list[dict]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in messages)


async def check_cache(db: AsyncSession, messages: list[dict]) -> dict | None:
    """
    Two-stage cache lookup:
      1. Exact hash match    — O(1), index lookup
      2. Vector similarity   — pgvector HNSW index, O(log n), runs in Postgres

    Returns cached result dict on hit, None on miss.
    """
    cache_text = _messages_to_text(messages)
    exact_hash = _prompt_hash(cache_text)

    # ── Stage 1: exact match ──────────────────────────────────────────
    exact = await db.execute(
        select(CacheEntry).where(
            CacheEntry.prompt_hash == exact_hash,
            CacheEntry.is_stale == False,
        )
    )
    row = exact.scalar_one_or_none()

    if row:
        await _increment_hit(db, row)
        return _to_result(row, similarity=1.0)

    # ── Stage 2: pgvector similarity search ──────────────────────────
    # <=> is cosine distance operator (0 = identical, 2 = opposite)
    # We convert to similarity: similarity = 1 - distance
    query_vec = _embed(cache_text)

    result = await db.execute(
        text("""
            SELECT
                id,
                response_text,
                provider,
                model,
                input_tokens,
                output_tokens,
                hit_count,
                saved_cost_usd,
                1 - (embedding <=> CAST(:vec AS vector)) AS similarity
            FROM cache_entries
            WHERE is_stale = FALSE
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT 1
        """),
        {"vec": str(query_vec)}
    )

    best = result.fetchone()

    if best and best.similarity >= SIMILARITY_THRESHOLD:
        # Fetch full ORM object to update hit count
        orm_row = await db.get(CacheEntry, best.id)
        if orm_row:
            await _increment_hit(db, orm_row)
        return {
            "content":       best.response_text,
            "provider":      best.provider,
            "model":         best.model,
            "input_tokens":  best.input_tokens,
            "output_tokens": best.output_tokens,
            "cost_usd":      0.0,
            "latency_ms":    0,
            "cache_hit":     True,
            "similarity":    round(float(best.similarity), 4),
        }

    return None


async def store_in_cache(db: AsyncSession, messages: list[dict], response: dict) -> None:
    """Store LLM response in cache with pgvector embedding."""
    cache_text = _messages_to_text(messages)
    exact_hash = _prompt_hash(cache_text)

    # Guard against duplicate inserts (race condition)
    existing = await db.execute(
        select(CacheEntry).where(CacheEntry.prompt_hash == exact_hash)
    )
    if existing.scalar_one_or_none():
        return

    embedding_vec = _embed(cache_text)

    entry = CacheEntry(
        id            = str(uuid.uuid4()),
        prompt_hash   = exact_hash,
        prompt_text   = cache_text[:2000],
        embedding     = embedding_vec,
        response_text = response["content"],
        provider      = response["provider"],
        model         = response["model"],
        input_tokens  = response["input_tokens"],
        output_tokens = response["output_tokens"],
        hit_count     = 0,
        saved_cost_usd= 0.0,
        is_stale      = False,
    )

    db.add(entry)
    await db.commit()


async def _increment_hit(db: AsyncSession, row: CacheEntry) -> None:
    cost_saved = calculate_cost(row.provider, row.model, row.input_tokens, row.output_tokens)
    await db.execute(
        update(CacheEntry)
        .where(CacheEntry.id == row.id)
        .values(
            hit_count      = CacheEntry.hit_count + 1,
            saved_cost_usd = CacheEntry.saved_cost_usd + cost_saved,
        )
    )
    await db.commit()


def _to_result(row: CacheEntry, similarity: float) -> dict:
    return {
        "content":       row.response_text,
        "provider":      row.provider,
        "model":         row.model,
        "input_tokens":  row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd":      0.0,
        "latency_ms":    0,
        "cache_hit":     True,
        "similarity":    similarity,
    }


async def get_cache_stats(db: AsyncSession) -> dict:
    result = await db.execute(select(CacheEntry))
    rows = result.scalars().all()

    if not rows:
        return {
            "total_entries":   0,
            "total_hits":      0,
            "total_saved_usd": 0.0,
            "threshold":       SIMILARITY_THRESHOLD,
            "top_cached":      [],
        }

    return {
        "total_entries":   len(rows),
        "total_hits":      sum(r.hit_count for r in rows),
        "total_saved_usd": round(sum(r.saved_cost_usd for r in rows), 8),
        "threshold":       SIMILARITY_THRESHOLD,
        "top_cached": [
            {
                "prompt_preview": r.prompt_text[:80] + "..." if len(r.prompt_text) > 80 else r.prompt_text,
                "hit_count":      r.hit_count,
                "saved_usd":      round(r.saved_cost_usd, 8),
            }
            for r in sorted(rows, key=lambda x: x.hit_count, reverse=True)[:5]
        ],
    }