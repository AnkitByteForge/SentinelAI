#FastAPI app entry point

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

# Allow the Next.js dev server to call the API from the browser.
# (Browser requests include a CORS preflight when using Authorization headers.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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