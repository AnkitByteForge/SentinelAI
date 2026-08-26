 # settings + env vars
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    groq_api_key: str = ""
    gemini_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./sentinelai.db"
    postgres_url:   str = "postgresql+asyncpg://sentinel:sentinel_dev_pass@localhost:5432/sentinelai"

    environment: str = "development"

    # Master admin key — always valid, bypasses per-key rate limiting.
    # Used to bootstrap the system and to authenticate /v1/keys management endpoints.
    # Per-tenant keys (see app/services/api_keys.py) are the normal auth path for /v1/chat.
    api_key: str = "sentinel-dev-key-123"

    # Performance / profiling toggles
    # - PRELOAD_EMBEDDING_MODEL=true  => load SentenceTransformer at process start
    # - LOG_STAGE_TIMINGS=true        => print per-stage timings in /v1/chat
    preload_embedding_model: bool = False
    log_stage_timings: bool = False

    # ── Redis ──────────────────────────────────────────────────────────
    # Backs the Celery broker/result backend, the API key lookup cache,
    # and per-key rate limiting.
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── API key management ────────────────────────────────────────────
    api_key_cache_ttl_seconds: int = 60
    default_rate_limit_per_minute: int = 100

    # ── Circuit breaker webhook ────────────────────────────────────────
    # WEBHOOK_URL unset => webhook delivery is a no-op (checked before every send).
    webhook_url: str | None = None
    webhook_secret: str | None = None
    webhook_timeout_seconds: float = 5.0

    # ── Health checks ─────────────────────────────────────────────────
    health_db_timeout_seconds: float = 2.0
    health_redis_timeout_seconds: float = 1.0
    celery_queue_depth_degraded_threshold: int = 50

    class Config:
        env_file = ".env"

settings = Settings()