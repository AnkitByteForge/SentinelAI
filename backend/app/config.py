 # settings + env vars
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    groq_api_key: str = ""
    gemini_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./sentinelai.db"
    environment: str = "development"
    api_key: str = "sentinel-dev-key-123"

    class Config:
        env_file = ".env"

settings = Settings()