# ORM models (requests table)

from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, func
from sqlalchemy.dialects.sqlite import TEXT
from app.db.database import Base
import uuid

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
    created_at    = Column(DateTime, server_default=func.now())