import hashlib
import json
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
import uuid

from app.db.models import CacheEntry
from app.services.cost import calculate_cost

# ── Load model once at import time (not per request) ─────────────────
# Downloads ~90MB on first run, then cached locally forever
_model = None
_model_load_error: str | None = None


def _get_model():
    global _model, _model_load_error
    if _model is not None:
        return _model
    if _model_load_error is not None:
        return None

    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("all-MiniLM-L6-v2")
        return _model
    except Exception as e:
        _model_load_error = str(e)
        return None

SIMILARITY_THRESHOLD = 0.92   # tunable — explained below


def _embed(text: str) -> list[float]:
    """Generate 384-dim embedding vector for a text string."""
    model = _get_model()
    if model is None:
        raise RuntimeError("Embedding model unavailable")

    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity between two normalized vectors.
    Since we normalize at embedding time, this is just a dot product.
    Range: -1.0 to 1.0. For similar text: typically 0.85–0.99.
    """
    vec_a = np.array(a, dtype=np.float32)
    vec_b = np.array(b, dtype=np.float32)
    return float(np.dot(vec_a, vec_b))


def _prompt_hash(text: str) -> str:
    """SHA256 hash of prompt text — for exact-match fast path."""
    return hashlib.sha256(text.strip().encode()).hexdigest()


def _messages_to_cache_key(messages: list[dict]) -> str:
    """
    Convert message list to a single string for embedding.
    We use the full conversation context, not just the last message.
    This ensures "What is ML?" and "What is ML? (after system prompt X)"
    are treated differently if system prompts differ.
    """
    parts = []
    for msg in messages:
        parts.append(f"{msg['role']}: {msg['content']}")
    return "\n".join(parts)


async def check_cache(
    db: AsyncSession,
    messages: list[dict],
) -> dict | None:
    """
    Check if a semantically similar request is cached.

    Returns cached result dict if hit, None if miss.
    Two-stage lookup:
      1. Exact hash match (fast, O(1))
      2. Cosine similarity search (slower, O(n) — acceptable at this scale)
    """
    cache_text = _messages_to_cache_key(messages)
    exact_hash = _prompt_hash(cache_text)

    # ── Stage 1: exact hash match ─────────────────────────────────────
    exact_stmt = select(CacheEntry).where(
        CacheEntry.prompt_hash == exact_hash,
        CacheEntry.is_stale == False,
    )
    exact_row = (await db.execute(exact_stmt)).scalar_one_or_none()

    if exact_row:
        await _increment_hit(db, exact_row, calculate_cost(
            exact_row.provider, exact_row.model,
            exact_row.input_tokens, exact_row.output_tokens
        ))
        return {
            "content":       exact_row.response_text,
            "provider":      exact_row.provider,
            "model":         exact_row.model,
            "input_tokens":  exact_row.input_tokens,
            "output_tokens": exact_row.output_tokens,
            "cost_usd":      0.0,   # cache hits cost nothing
            "latency_ms":    0,     # will be measured end-to-end
            "cache_hit":     True,
            "similarity":    1.0,   # exact match
        }

    # ── Stage 2: semantic similarity search ───────────────────────────
    try:
        query_embedding = _embed(cache_text)
    except Exception:
        return None

    all_stmt = select(CacheEntry).where(CacheEntry.is_stale == False)
    all_rows = (await db.execute(all_stmt)).scalars().all()

    best_row = None
    best_sim = 0.0

    for row in all_rows:
        sim = _cosine_similarity(query_embedding, row.embedding)
        if sim > best_sim:
            best_sim = sim
            best_row = row

    if best_row and best_sim >= SIMILARITY_THRESHOLD:
        await _increment_hit(db, best_row, calculate_cost(
            best_row.provider, best_row.model,
            best_row.input_tokens, best_row.output_tokens
        ))
        return {
            "content":       best_row.response_text,
            "provider":      best_row.provider,
            "model":         best_row.model,
            "input_tokens":  best_row.input_tokens,
            "output_tokens": best_row.output_tokens,
            "cost_usd":      0.0,
            "latency_ms":    0,
            "cache_hit":     True,
            "similarity":    round(best_sim, 4),
        }

    # ── Miss — return None ────────────────────────────────────────────
    return None


async def store_in_cache(
    db: AsyncSession,
    messages: list[dict],
    response: dict,
) -> None:
    """
    Store a new LLM response in the cache.
    Called after every successful LLM call (cache miss path).
    """
    cache_text    = _messages_to_cache_key(messages)
    exact_hash    = _prompt_hash(cache_text)
    try:
        embedding_vec = _embed(cache_text)
    except Exception:
        return

    # Check if this exact hash already exists (race condition guard)
    existing = (await db.execute(
        select(CacheEntry).where(CacheEntry.prompt_hash == exact_hash)
    )).scalar_one_or_none()

    if existing:
        return   # already cached, skip

    entry = CacheEntry(
        id             = str(uuid.uuid4()),
        prompt_hash    = exact_hash,
        prompt_text    = cache_text[:2000],   # truncate for storage, embedding is the real key
        is_stale       = False,
        response_text  = response["content"],
        provider       = response["provider"],
        model          = response["model"],
        input_tokens   = response["input_tokens"],
        output_tokens  = response["output_tokens"],
        hit_count      = 0,
        saved_cost_usd = 0.0,
    )
    entry.embedding = embedding_vec   # uses the property setter

    db.add(entry)
    await db.commit()


async def _increment_hit(db: AsyncSession, row: CacheEntry, cost_saved: float) -> None:
    """Increment hit counter and accumulate cost saved. Fire and don't wait."""
    await db.execute(
        update(CacheEntry)
        .where(CacheEntry.id == row.id)
        .values(
            hit_count      = CacheEntry.hit_count + 1,
            saved_cost_usd = CacheEntry.saved_cost_usd + cost_saved,
        )
    )
    await db.commit()


async def get_cache_stats(db: AsyncSession) -> dict:
    """Summary stats for the cache — used in /v1/cache/stats endpoint."""
    all_rows = (await db.execute(select(CacheEntry))).scalars().all()

    if not all_rows:
        return {
            "total_entries":   0,
            "total_hits":      0,
            "total_saved_usd": 0.0,
            "threshold":       SIMILARITY_THRESHOLD,
        }

    return {
        "total_entries":   len(all_rows),
        "total_hits":      sum(r.hit_count for r in all_rows),
        "total_saved_usd": round(sum(r.saved_cost_usd for r in all_rows), 8),
        "threshold":       SIMILARITY_THRESHOLD,
        "top_cached": [
            {
                "prompt_preview": r.prompt_text[:80] + "..." if len(r.prompt_text) > 80 else r.prompt_text,
                "hit_count":      r.hit_count,
                "saved_usd":      round(r.saved_cost_usd, 8),
            }
            for r in sorted(all_rows, key=lambda x: x.hit_count, reverse=True)[:5]
        ],
    }