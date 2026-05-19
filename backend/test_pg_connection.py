import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

POSTGRES_URL = "postgresql+asyncpg://sentinel:sentinel_dev_pass@localhost:5432/sentinelai"

async def test():
    engine = create_async_engine(POSTGRES_URL, echo=False)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version()"))
        row = result.fetchone()
        print("\n✅ Postgres connected successfully")
        print(f"   Version: {row[0][:50]}")

        # Check pgvector
        result2 = await conn.execute(
            text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'")
        )
        count = result2.scalar()
        if count:
            print("✅ pgvector extension is available")
        else:
            print("❌ pgvector NOT found — run CREATE EXTENSION vector; in psql")

    await engine.dispose()
    print("\nReady for Step 2: schema migration\n")

asyncio.run(test())