"""HMAC signing and delivery behavior for circuit-breaker webhooks."""
import hashlib
import hmac

import httpx
import pytest

from app.config import settings
from app.services import webhook


@pytest.fixture(autouse=True)
def _reset_webhook_settings():
    original_url, original_secret = settings.webhook_url, settings.webhook_secret
    yield
    settings.webhook_url, settings.webhook_secret = original_url, original_secret


async def test_no_url_configured_is_a_clean_noop():
    settings.webhook_url = None
    result = await webhook.send_webhook({"event": "test"})
    assert result == {"sent": False, "status_code": None, "error": "no webhook_url configured"}


def test_signature_is_deterministic_hmac_sha256():
    settings.webhook_secret = "test-secret"
    body = b'{"event":"circuit_breaker.opened"}'
    sig1 = webhook._sign(body)
    sig2 = webhook._sign(body)
    assert sig1 == sig2 == hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()


def test_no_secret_configured_means_no_signature():
    settings.webhook_secret = None
    assert webhook._sign(b"anything") is None


async def test_delivery_success_reports_status_code(monkeypatch):
    settings.webhook_url = "https://example.invalid/hook"
    settings.webhook_secret = "shh"

    captured = {}

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, content, headers):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = content
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await webhook.send_webhook({"event": "circuit_breaker.opened", "provider": "groq"})

    assert result == {"sent": True, "status_code": 200, "error": None}
    assert captured["url"] == "https://example.invalid/hook"
    assert "X-Sentinel-Signature" in captured["headers"]
    expected_sig = hmac.new(b"shh", captured["body"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Sentinel-Signature"] == expected_sig


async def test_delivery_failure_never_raises(monkeypatch):
    settings.webhook_url = "https://example.invalid/hook"

    class FakeAsyncClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, *_a, **_k):
            raise httpx.ConnectTimeout("simulated timeout")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await webhook.send_webhook({"event": "test"})
    assert result["sent"] is False
    assert "simulated timeout" in result["error"]
