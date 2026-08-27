from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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
    """
    Ensures the pgvector extension exists (idempotent, needed before any
    migration touching a vector column can run) and nothing else.

    Schema is entirely Alembic-managed as of migrations 0001/0002 — run
    `alembic upgrade head` to create/update tables. This function used to
    also call Base.metadata.create_all(), which can create tables but
    can't alter existing ones, making it unsafe as a schema-management
    strategy once the schema needs to change.
    """
    async with engine.begin() as conn:
        from sqlalchemy import text
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
