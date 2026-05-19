import uuid
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, func
from pgvector.sqlalchemy import Vector

from app.db.database import Base


class RequestLog(Base):
    __tablename__ = "requests"

    id            = Column(String,  primary_key=True, default=lambda: str(uuid.uuid4()))
    provider      = Column(String,  nullable=False)
    model         = Column(String,  nullable=False)
    input_tokens  = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd      = Column(Float,   default=0.0)
    latency_ms    = Column(Integer, default=0)
    cache_hit     = Column(Boolean, default=False)
    status        = Column(String,  default="success")
    error_type    = Column(String,  nullable=True)
    fallback_from = Column(String,  nullable=True)
    prompt_preview  = Column(Text,  nullable=True)
    response_preview= Column(Text,  nullable=True)
    created_at    = Column(DateTime, server_default=func.now())


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    id            = Column(String,  primary_key=True, default=lambda: str(uuid.uuid4()))
    prompt_hash   = Column(String,  nullable=False, unique=True, index=True)
    prompt_text   = Column(Text,    nullable=False)

    # Real vector column (384 dims for all-MiniLM-L6-v2)
    # Replaces the old embedding_json TEXT column entirely
    embedding     = Column(Vector(384), nullable=False)

    response_text = Column(Text,    nullable=False)
    provider      = Column(String,  nullable=False)
    model         = Column(String,  nullable=False)
    input_tokens  = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    hit_count     = Column(Integer, default=0)
    saved_cost_usd= Column(Float,   default=0.0)
    is_stale      = Column(Boolean, default=False)
    created_at    = Column(DateTime, server_default=func.now())