#FastAPI app entry point

from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.routers import gateway
from app.routers import observability
from app.db.database import init_db
from app.services.circuit_breaker import registry as cb 

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
app.include_router(observability.router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "circuit_breakers": cb.get_all_states(),   # ← add
    }