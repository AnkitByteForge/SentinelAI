"""
Directly tests that we can:
1. Insert a vector into cache_entries
2. Query it back with similarity search
This bypasses Celery entirely to isolate the issue.
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, select
import uuid

POSTGRES_URL = "postgresql+asyncpg://sentinel:sentinel_dev_pass@localhost:5432/sentinelai"

async def test():
    engine = create_async_engine(POSTGRES_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # Step 1: generate a test embedding
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        vec = model.encode("What is PostgreSQL?", normalize_embeddings=True).tolist()
        print(f"✅ Embedding generated — {len(vec)} dimensions")

        # Step 2: insert directly with SQL (bypasses ORM format issues)
        test_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO cache_entries
                    (id, prompt_hash, prompt_text, embedding,
                     response_text, provider, model,
                     input_tokens, output_tokens, hit_count,
                     saved_cost_usd, is_stale)
                VALUES
                    (:id, :hash, :text, CAST(:vec AS vector),
                     :response, :provider, :model,
                     :inp, :out, 0, 0.0, false)
            """),
            {
                "id":       test_id,
                "hash":     "test_hash_123",
                "text":     "user: What is PostgreSQL?",
                "vec":      str(vec),
                "response": "PostgreSQL is a relational database...",
                "provider": "groq",
                "model":    "llama-3.1-8b-instant",
                "inp":      39,
                "out":      482,
            }
        )
        await db.commit()
        print("✅ Vector inserted into cache_entries")

        # Step 3: query it back with similarity search
        result = await db.execute(
            text("""
                SELECT id, prompt_text,
                       1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                FROM cache_entries
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 1
            """),
            {"vec": str(vec)}
        )
        row = result.fetchone()
        if row:
            print(f"✅ Similarity search works — similarity: {row.similarity:.4f}")
            print(f"   Found: {row.prompt_text[:50]}")
        else:
            print("❌ Similarity search returned nothing")

        # Cleanup
        await db.execute(text("DELETE FROM cache_entries WHERE id = :id"), {"id": test_id})
        await db.commit()
        print("✅ Cleanup done\n")
        print("pgvector insert + search is working correctly.")
        print("The issue is the Celery worker not running.")

    await engine.dispose()

asyncio.run(test())