from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────
# pool_pre_ping=True — verifies connection is alive before using it
# pool_size=10       — up to 10 concurrent DB connections
# max_overflow=20    — allow 20 extra connections under spike load
engine = create_async_engine(
    settings.postgres_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """Create all tables. Will be replaced by Alembic in Step 3."""
    async with engine.begin() as conn:
        # Enable pgvector extension first — idempotent
        from sqlalchemy import text
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.commit()

    # Create tables
    async with engine.begin() as conn:
        from app.db.models import RequestLog, CacheEntry
        await conn.run_sync(Base.metadata.create_all)