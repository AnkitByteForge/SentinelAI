# Pydantic request/response models
from pydantic import BaseModel
from typing import Optional

class Message(BaseModel):
    role: str       # "system" | "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]
    model: Optional[str] = "llama-3.1-8b-instant"   # Groq default
    max_tokens: Optional[int] = 1000
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