# ORM models (requests table)

from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, func
from sqlalchemy.dialects.sqlite import TEXT
from app.db.database import Base
import uuid
import json

class RequestLog(Base):
    __tablename__ = "requests"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider      = Column(String, nullable=False)
    model         = Column(String, nullable=False)
    input_tokens  = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd      = Column(Float, default=0.0)
    latency_ms    = Column(Integer, default=0)
    cache_hit     = Column(Boolean, default=False)
    status        = Column(String, default="success")   # success | error | fallback
    error_type    = Column(String, nullable=True)
    fallback_from = Column(String, nullable=True)
    prompt_preview  = Column(TEXT, nullable=True)
    response_preview= Column(TEXT, nullable=True)
    created_at    = Column(DateTime, server_default=func.now())


    import json

class CacheEntry(Base):
    __tablename__ = "cache_entries"

    id            = Column(String,  primary_key=True, default=lambda: str(uuid.uuid4()))
    prompt_hash   = Column(String,  nullable=False, unique=True, index=True)
    prompt_text   = Column(TEXT,    nullable=False)
    embedding_json= Column(TEXT,    nullable=False)   # stored as JSON string
    response_text = Column(TEXT,    nullable=False)
    provider      = Column(String,  nullable=False)
    model         = Column(String,  nullable=False)
    input_tokens  = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    hit_count     = Column(Integer, default=0)
    saved_cost_usd= Column(Float,   default=0.0)     # cumulative cost saved on hits
    is_stale      = Column(Boolean, default=False)    # manual invalidation flag
    created_at    = Column(DateTime, server_default=func.now())

    @property
    def embedding(self) -> list[float]:
        return json.loads(self.embedding_json)

    @embedding.setter
    def embedding(self, value: list[float]):
        self.embedding_json = json.dumps(value)