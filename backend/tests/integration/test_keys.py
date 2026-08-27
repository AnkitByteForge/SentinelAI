"""POST/GET /v1/keys, DELETE /v1/keys/{id}, POST /v1/keys/{id}/rotate — master-key-only."""


async def test_create_key_returns_raw_key_once(client, master_headers):
    resp = await client.post("/v1/keys", headers=master_headers, json={"name": "prod", "rate_limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"].startswith("sk-sent-")
    assert body["name"] == "prod"
    assert body["rate_limit"] == 50
    assert body["key_prefix"] in body["key"]


async def test_create_key_uses_default_rate_limit_when_unspecified(client, master_headers):
    resp = await client.post("/v1/keys", headers=master_headers, json={"name": "no-limit-specified"})
    assert resp.status_code == 200
    assert resp.json()["rate_limit"] == 100  # settings.default_rate_limit_per_minute


async def test_list_keys_never_returns_the_raw_key(client, master_headers):
    await client.post("/v1/keys", headers=master_headers, json={"name": "listed", "rate_limit": 10})
    resp = await client.get("/v1/keys", headers=master_headers)

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    for row in rows:
        assert "key" not in row
        assert "key_hash" not in row
        assert "key_prefix" in row


async def test_delete_key_soft_deletes(client, master_headers):
    create_resp = await client.post("/v1/keys", headers=master_headers, json={"name": "delete-me", "rate_limit": 10})
    key_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/v1/keys/{key_id}", headers=master_headers)
    assert delete_resp.status_code == 200

    rows = (await client.get("/v1/keys", headers=master_headers)).json()
    row = next(r for r in rows if r["id"] == key_id)
    assert row["is_active"] is False


async def test_delete_unknown_key_returns_404(client, master_headers):
    resp = await client.delete("/v1/keys/not-a-real-id", headers=master_headers)
    assert resp.status_code == 404


async def test_rotate_key_issues_a_new_raw_key(client, master_headers):
    create_resp = await client.post("/v1/keys", headers=master_headers, json={"name": "rotate-me", "rate_limit": 10})
    key_id, old_key = create_resp.json()["id"], create_resp.json()["key"]

    rotate_resp = await client.post(f"/v1/keys/{key_id}/rotate", headers=master_headers)
    assert rotate_resp.status_code == 200
    new_key = rotate_resp.json()["key"]
    assert new_key != old_key
    assert rotate_resp.json()["id"] == key_id


async def test_rotate_unknown_key_returns_404(client, master_headers):
    resp = await client.post("/v1/keys/not-a-real-id/rotate", headers=master_headers)
    assert resp.status_code == 404


async def test_tenant_key_cannot_call_key_management_endpoints(client, master_headers):
    create_resp = await client.post("/v1/keys", headers=master_headers, json={"name": "not-admin", "rate_limit": 10})
    tenant_key = create_resp.json()["key"]

    resp = await client.get("/v1/keys", headers={"Authorization": f"Bearer {tenant_key}"})
    assert resp.status_code == 401
