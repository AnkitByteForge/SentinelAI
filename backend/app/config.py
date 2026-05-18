 # settings + env vars
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    groq_api_key: str = ""
    gemini_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./sentinelai.db"
    environment: str = "development"
    api_key: str = "sentinel-dev-key-123"

    # Performance / profiling toggles
    # - PRELOAD_EMBEDDING_MODEL=true  => load SentenceTransformer at process start
    # - LOG_STAGE_TIMINGS=true        => print per-stage timings in /v1/chat
    preload_embedding_model: bool = False
    log_stage_timings: bool = False

    class Config:
        env_file = ".env"

settings = Settings()