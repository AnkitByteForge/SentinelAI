"""/v1/circuit/*, /health*, /v1/webhook/* — the observability surface."""
from app.services.circuit_breaker import registry as cb


async def test_circuit_states_reports_closed_by_default(client, master_headers):
    resp = await client.get("/v1/circuit/states", headers=master_headers)
    assert resp.status_code == 200
    assert resp.json() == {}  # nothing recorded yet


async def test_circuit_reset_clears_an_open_circuit(client, master_headers):
    for _ in range(3):
        await cb.record_failure("groq")
    assert await cb.get_state("groq") == "open"

    resp = await client.post("/v1/circuit/groq/reset", headers=master_headers)
    assert resp.status_code == 200
    assert await cb.get_state("groq") == "closed"


async def test_health_live_never_checks_dependencies(client):
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


async def test_health_returns_200_when_everything_is_up(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "database" in body["checks"]
    assert "redis" in body["checks"]


async def test_health_ready_matches_health(client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_health_does_not_require_auth(client):
    # No Authorization header at all — /health must stay reachable for
    # uptime monitors that don't (and shouldn't need to) know an API key.
    resp = await client.get("/health")
    assert resp.status_code in (200, 503)


async def test_webhook_config_reports_unconfigured_by_default(client, master_headers):
    resp = await client.get("/v1/webhook/config", headers=master_headers)
    assert resp.status_code == 200
    assert resp.json() == {"url_configured": False, "secret_configured": False}


async def test_webhook_test_without_url_configured_returns_400(client, master_headers):
    resp = await client.post("/v1/webhook/test", headers=master_headers)
    assert resp.status_code == 400
