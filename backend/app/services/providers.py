# Groq + Gemini clients

import httpx
import time
from app.config import settings
from app.services.cost import calculate_cost

# ─── Groq (OpenAI-compatible, free tier) ───────────────────────────
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

async def call_groq(messages: list[dict], model: str, max_tokens: int, temperature: float) -> dict:
    """Call Groq API. Returns normalized response dict."""
    start = time.monotonic()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        resp.raise_for_status()
        data = resp.json()

    latency_ms = int((time.monotonic() - start) * 1000)
    choice = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})

    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    return {
        "content": choice,
        "provider": "groq",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "cost_usd": calculate_cost("groq", model, input_tokens, output_tokens),
    }


# ─── Gemini (free tier via REST) ───────────────────────────────────
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

def _convert_to_gemini_format(messages: list[dict]) -> tuple[str, list[dict]]:
    """Convert OpenAI message format to Gemini format."""
    system_prompt = ""
    contents = []

    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        elif msg["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
        elif msg["role"] == "assistant":
            contents.append({"role": "model", "parts": [{"text": msg["content"]}]})

    return system_prompt, contents

async def call_gemini(messages: list[dict], max_tokens: int, temperature: float) -> dict:
    """Call Gemini 2.5 Flash (free tier). Returns normalized response dict."""
    start = time.monotonic()
    model = "gemini-2.5-flash"
    system_prompt, contents = _convert_to_gemini_format(messages)

    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        }
    }
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{GEMINI_BASE_URL}/models/{model}:generateContent",
            params={"key": settings.gemini_api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    latency_ms = int((time.monotonic() - start) * 1000)
    content = data["candidates"][0]["content"]["parts"][0]["text"]
    usage = data.get("usageMetadata", {})

    input_tokens = usage.get("promptTokenCount", 0)
    output_tokens = usage.get("candidatesTokenCount", 0)

    return {
        "content": content,
        "provider": "gemini",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "cost_usd": calculate_cost("gemini", model, input_tokens, output_tokens),
    }
