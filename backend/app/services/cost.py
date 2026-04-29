# Real 2025 pricing per 1M tokens (input / output)
# Source: official provider pricing pages
PRICING = {
    "groq": {
        # Groq charges these rates — free tier gives credits, same calculation
        "llama-3.1-8b-instant":   {"input": 0.05,  "output": 0.08},   # per 1M tokens
        "llama-3.1-70b-versatile":{"input": 0.59,  "output": 0.79},
        "llama-3.3-70b-versatile":{"input": 0.59,  "output": 0.79},
        "mixtral-8x7b-32768":     {"input": 0.24,  "output": 0.24},
        "gemma2-9b-it":           {"input": 0.20,  "output": 0.20},
    },
    "gemini": {

      "gemini-2.5-flash":      {"input": 0.15,  "output": 0.60},   # ← add
      "gemini-2.5-flash-lite": {"input": 0.075, "output": 0.30},   # ← add
      "gemini-2.0-flash":      {"input": 0.10,  "output": 0.40},
      "gemini-1.5-flash":      {"input": 0.075, "output": 0.30},
    },
    "openai": {
        # For future reference / comparison
        "gpt-4o":                 {"input": 2.50,  "output": 10.00},
        "gpt-4o-mini":            {"input": 0.15,  "output": 0.60},
        "gpt-3.5-turbo":          {"input": 0.50,  "output": 1.50},
    },
}

def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate real USD cost based on token counts and provider pricing.
    Returns 0.0 if provider/model not found (safe fallback).
    """
    provider_pricing = PRICING.get(provider, {})
    model_pricing = provider_pricing.get(model)

    if not model_pricing:
        return 0.0

    input_cost  = (input_tokens  / 1_000_000) * model_pricing["input"]
    output_cost = (output_tokens / 1_000_000) * model_pricing["output"]

    return round(input_cost + output_cost, 8)   # 8 decimal places for small amounts


def get_pricing_table() -> dict:
    """Returns full pricing table — used by the /v1/pricing endpoint."""
    return PRICING