"""
POST /v1/chat end-to-end: cache miss/hit, provider fallback, all-providers-down,
auth (master key / tenant key / invalid key), and per-key rate limiting.

Providers are mocked (see conftest.mock_providers) — this suite is testing
the gateway's own routing/caching/auth logic, not Groq or Gemini.
"""
import pytest


def _chat_body(prompt: str = "What is PostgreSQL?") -> dict:
    return {"messages": [{"role": "user", "content": prompt}], "max_tokens": 50}


async def test_cache_miss_calls_groq_and_returns_success(client, master_headers, mock_providers):
    resp = await client.post("/v1/chat", headers=master_headers, json=_chat_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "groq"
    assert body["meta"]["cache_hit"] is False
    assert mock_providers.calls["groq"] == 1


async def test_repeated_prompt_is_a_cache_hit(client, master_headers, mock_providers):
    prompt = "What is a circuit breaker pattern?"
    first = await client.post("/v1/chat", headers=master_headers, json=_chat_body(prompt))
    assert first.json()["meta"]["cache_hit"] is False

    second = await client.post("/v1/chat", headers=master_headers, json=_chat_body(prompt))
    body = second.json()
    assert body["meta"]["cache_hit"] is True
    assert body["usage"]["cost_usd"] == 0.0
    # Only the first request should have actually called a provider.
    assert mock_providers.calls["groq"] == 1


async def test_bypass_cache_skips_the_cache_even_on_repeat(client, master_headers, mock_providers):
    prompt = "What is pgvector?"
    body = _chat_body(prompt)
    body["bypass_cache"] = True

    await client.post("/v1/chat", headers=master_headers, json=body)
    second = await client.post("/v1/chat", headers=master_headers, json=body)

    assert second.json()["meta"]["cache_hit"] is False
    assert mock_providers.calls["groq"] == 2


async def test_groq_failure_falls_back_to_gemini(client, master_headers, mock_providers):
    mock_providers.set("groq", "fail")
    resp = await client.post("/v1/chat", headers=master_headers, json=_chat_body("unique prompt one"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["meta"]["fallback"] == "groq"


async def test_both_providers_down_returns_503_with_circuit_states(client, master_headers, mock_providers):
    mock_providers.set("groq", "fail")
    mock_providers.set("gemini", "fail")

    resp = await client.post("/v1/chat", headers=master_headers, json=_chat_body("unique prompt two"))
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error"] == "All providers unavailable"
    assert "circuit_states" in detail


async def test_missing_auth_header_is_rejected(client, mock_providers):
    resp = await client.post("/v1/chat", json=_chat_body())
    assert resp.status_code in (401, 422)  # 422 if FastAPI rejects the missing required header first


async def test_invalid_bearer_token_is_rejected(client, mock_providers):
    resp = await client.post(
        "/v1/chat", headers={"Authorization": "Bearer not-a-real-key"}, json=_chat_body()
    )
    assert resp.status_code == 401


async def test_tenant_key_can_call_chat_and_gets_rate_limit_headers(client, master_headers, mock_providers):
    create_resp = await client.post(
        "/v1/keys", headers=master_headers, json={"name": "chat-test-key", "rate_limit": 5}
    )
    tenant_key = create_resp.json()["key"]
    headers = {"Authorization": f"Bearer {tenant_key}"}

    resp = await client.post("/v1/chat", headers=headers, json=_chat_body("tenant key prompt"))
    assert resp.status_code == 200
    assert resp.headers["X-RateLimit-Limit"] == "5"
    assert resp.headers["X-RateLimit-Remaining"] == "4"


async def test_tenant_key_rate_limit_returns_429_with_headers(client, master_headers, mock_providers):
    create_resp = await client.post(
        "/v1/keys", headers=master_headers, json={"name": "throttled-key", "rate_limit": 1}
    )
    tenant_key = create_resp.json()["key"]
    headers = {"Authorization": f"Bearer {tenant_key}"}

    ok = await client.post("/v1/chat", headers=headers, json=_chat_body("first"))
    assert ok.status_code == 200

    throttled = await client.post("/v1/chat", headers=headers, json=_chat_body("second"))
    assert throttled.status_code == 429
    assert throttled.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in throttled.headers


async def test_revoked_tenant_key_is_rejected(client, master_headers, mock_providers):
    create_resp = await client.post(
        "/v1/keys", headers=master_headers, json={"name": "to-revoke", "rate_limit": 100}
    )
    key_id, tenant_key = create_resp.json()["id"], create_resp.json()["key"]

    await client.delete(f"/v1/keys/{key_id}", headers=master_headers)

    resp = await client.post(
        "/v1/chat", headers={"Authorization": f"Bearer {tenant_key}"}, json=_chat_body()
    )
    assert resp.status_code == 401
