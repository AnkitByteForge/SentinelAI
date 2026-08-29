# Pydantic request/response model
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    role: str       # "system" | "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]
    model: Optional[str] = "openai/gpt-oss-20b"   # Groq default — llama-3.1-8b-instant was retired by Groq
    # Raised from 1000 — gpt-oss-20b supports up to 65,536, and callers were
    # getting truncated responses on anything moderately long. Still well
    # under Groq's free-tier 8,000 tokens/minute cap for a single request.
    max_tokens: Optional[int] = 2048
    temperature: Optional[float] = 0.7
    bypass_cache: Optional[bool] = False

class UsageInfo(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float             # always 0.0 for free tiers, but field exists

class MetaInfo(BaseModel):
    cache_hit: bool
    latency_ms: int
    provider: str
    fallback: Optional[str] = None   # original provider if fallback occurred

class ChatResponse(BaseModel):
    id: str
    content: str
    provider: str
    model: str
    usage: UsageInfo
    meta: MetaInfo


# ── Log entry (one row from requests table) ──────────────────────────
class LogEntry(BaseModel):
    id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    cache_hit: bool
    status: str
    error_type: Optional[str] = None
    fallback_from: Optional[str] = None
    prompt_preview: Optional[str] = None
    response_preview: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)   # allows building from SQLAlchemy ORM objects

class LogsResponse(BaseModel):
    total: int
    page: int
    limit: int
    logs: List[LogEntry]

# ── Metrics response ─────────────────────────────────────────────────
class ProviderStats(BaseModel):
    requests: int
    errors: int
    avg_latency_ms: float
    error_rate: float            # 0.0 → 1.0

class LatencyStats(BaseModel):
    p50_ms: float
    p95_ms: float
    p99_ms: float
    avg_ms: float

class MetricsResponse(BaseModel):
    window: str                  # "1h" | "6h" | "24h" | "7d"
    total_requests: int
    successful_requests: int
    failed_requests: int
    fallback_requests: int
    cache_hit_rate: float        # 0.0 → 1.0
    total_cost_usd: float
    latency: LatencyStats
    providers: dict[str, ProviderStats]
