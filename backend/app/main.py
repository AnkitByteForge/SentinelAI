#FastAPI app entry point

from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.routers import gateway
from app.db.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()      # creates DB tables on startup
    yield

app = FastAPI(
    title="SentinelAI Gateway",
    description="LLM gateway with failover, caching, and observability",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(gateway.router)

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}