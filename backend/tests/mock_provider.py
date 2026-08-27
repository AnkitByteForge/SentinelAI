"""
A minimal stand-in for Groq and Gemini, used for load testing so results
reflect SentinelAI's own capacity (DB pool, Redis, cache, auth) instead of
the real providers' free-tier rate limits.

Mimics just enough of each response shape for services/providers.py's
call_groq()/call_gemini() to parse successfully. Latency and failure rate
are runtime-configurable via query params, which is what
tests/locustfile.py's failover scenario uses to force a provider "outage"
mid-run without restarting anything.

Run standalone:
    uvicorn tests.mock_provider:app --host 0.0.0.0 --port 9000

Then point the gateway at it (see docker-compose.loadtest.yml):
    GROQ_BASE_URL=http://localhost:9000/groq
    GEMINI_BASE_URL=http://localhost:9000/gemini
"""
import asyncio
import random
import time

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(title="SentinelAI mock LLM provider")

# Mutable at runtime via POST /_control — lets a load test script force a
# 100% failure rate on one provider to simulate an outage mid-run.
_state = {
    "groq": {"latency_ms": 50, "failure_rate": 0.0},
    "gemini": {"latency_ms": 50, "failure_rate": 0.0},
}


@app.post("/_control/{provider}")
async def set_behavior(provider: str, latency_ms: int = 50, failure_rate: float = 0.0):
    """failure_rate: 0.0-1.0. Used by the load test's failover scenario."""
    if provider not in _state:
        raise HTTPException(status_code=404, detail="unknown provider")
    _state[provider] = {"latency_ms": latency_ms, "failure_rate": failure_rate}
    return {"provider": provider, **_state[provider]}


@app.get("/_control")
async def get_behavior():
    return _state


async def _simulate(provider: str) -> None:
    cfg = _state[provider]
    if cfg["latency_ms"]:
        await asyncio.sleep(cfg["latency_ms"] / 1000)
    if cfg["failure_rate"] and random.random() < cfg["failure_rate"]:
        raise HTTPException(status_code=503, detail=f"mock {provider} outage")


@app.post("/groq/chat/completions")
async def groq_chat_completions(request: Request):
    await _simulate("groq")
    body = await request.json()
    prompt_len = sum(len(m.get("content", "")) for m in body.get("messages", []))
    return {
        "choices": [{"message": {"role": "assistant", "content": "mock groq response"}}],
        "usage": {
            "prompt_tokens": max(1, prompt_len // 4),
            "completion_tokens": 20,
        },
    }


@app.post("/gemini/models/{model}:generateContent")
async def gemini_generate_content(model: str, request: Request):
    await _simulate("gemini")
    body = await request.json()
    prompt_len = sum(
        len(part.get("text", ""))
        for content in body.get("contents", [])
        for part in content.get("parts", [])
    )
    return {
        "candidates": [{"content": {"parts": [{"text": "mock gemini response"}]}}],
        "usageMetadata": {
            "promptTokenCount": max(1, prompt_len // 4),
            "candidatesTokenCount": 20,
        },
    }


@app.get("/health")
async def health():
    return {"status": "ok", "time": time.time()}
