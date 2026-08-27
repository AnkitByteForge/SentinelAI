#FastAPI app entry point

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging_config import configure_logging

configure_logging(settings.log_level)   # before other app imports, so their loggers inherit it

from app.db.database import get_db, init_db
from app.routers import gateway, keys, observability
from app.services.health import get_system_health

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()      # creates DB tables on startup
    if settings.preload_embedding_model:
        from app.services.cache import warmup_embedding_model

        # Run in a worker thread to avoid blocking the event loop.
        await asyncio.to_thread(warmup_embedding_model)

    try:
        yield
    finally:
        from app.services.providers import aclose_http_clients
        from app.services.redis_client import aclose_redis

        await aclose_http_clients()
        await aclose_redis()

app = FastAPI(
    title="SentinelAI Gateway",
    description="LLM gateway with failover, caching, and observability",
    version="0.1.0",
    lifespan=lifespan,
)

# Origins allowed to call the API from a browser — set via CORS_ALLOWED_ORIGINS.
# (Browser requests include a CORS preflight when using Authorization headers.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gateway.router)
app.include_router(observability.router)
app.include_router(keys.router)


@app.get("/health/live")
async def health_live():
    """
    Kubernetes liveness probe — returns 200 as long as the process is
    running and able to handle a request. No dependency checks; a slow
    database or Redis must not cause the orchestrator to kill the pod.
    """
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready(response: Response, db: AsyncSession = Depends(get_db)):
    """
    Kubernetes readiness probe — full dependency check (database, Redis,
    Celery, providers). Returns HTTP 503 when the database or Redis is
    unreachable, so the orchestrator stops routing traffic here.
    """
    result = await get_system_health(db)
    if result["status"] == "unhealthy":
        response.status_code = 503
    return result


@app.get("/health")
async def health(response: Response, db: AsyncSession = Depends(get_db)):
    """
    Detailed health check for uptime monitors (Better Uptime, UptimeRobot,
    Datadog). Same rollup as /health/ready — checks status code, not just
    the body, so an unreachable database/Redis returns HTTP 503.
    """
    result = await get_system_health(db)
    if result["status"] == "unhealthy":
        response.status_code = 503
    return result
