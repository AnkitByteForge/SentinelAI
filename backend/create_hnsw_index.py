"""
Run this ONCE after tables are created to add the HNSW index.
HNSW = Hierarchical Navigable Small World — O(log n) approximate nearest neighbour.
After this, every vector similarity query uses the index instead of scanning all rows.
"""
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text


def _get_database_url() -> str:
    """Resolve DB URL from env or app settings and normalize async driver."""
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        url = env_url
    else:
        try:
            from app.config import settings

            url = settings.database_url
        except Exception as e:
            raise RuntimeError(
                "DATABASE_URL not set and app.config.settings unavailable"
            ) from e

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError(
            f"Unsupported database URL for this script: {url}"
        )

    return url

async def create_index():
    url = _get_database_url()
    engine = create_async_engine(url, echo=True)

    try:
        async with engine.begin() as conn:
            # HNSW index for cosine distance (<=> operator)
            # m=16: connections per layer (higher = better recall, more memory)
            # ef_construction=64: search depth during build (higher = better quality, slower build)
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS cache_embedding_hnsw
                ON cache_entries
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """))
            print("\n✅ HNSW index created on cache_entries.embedding")
            print("   Vector similarity queries now use O(log n) index search")
            print("   instead of O(n) full table scan\n")
    except Exception as e:
        raise RuntimeError(
            "Failed to create HNSW index. Ensure the 'vector' extension exists "
            "and the cache_entries table is present."
        ) from e
    finally:
        await engine.dispose()

asyncio.run(create_index())