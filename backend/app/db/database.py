 # SQLAlchemy setup
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        from app.db.models import RequestLog   # import here to avoid circular
        await conn.run_sync(Base.metadata.create_all)

        # SQLite doesn't auto-migrate schema. Add new observability columns if missing.
        try:
            result = await conn.exec_driver_sql("PRAGMA table_info(requests)")
            rows = result.all()
            existing = {r[1] for r in rows}  # (cid, name, type, notnull, dflt_value, pk)

            if "prompt_preview" not in existing:
                await conn.exec_driver_sql("ALTER TABLE requests ADD COLUMN prompt_preview TEXT")
            if "response_preview" not in existing:
                await conn.exec_driver_sql("ALTER TABLE requests ADD COLUMN response_preview TEXT")
        except Exception:
            # Best-effort migration; if PRAGMA/ALTER fails we keep serving without previews.
            pass