# Circuit-breaker webhook delivery — signed, best-effort, never raises.
from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from app.config import settings


def _sign(payload_bytes: bytes) -> str | None:
    if not settings.webhook_secret:
        return None
    return hmac.new(settings.webhook_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


async def send_webhook(payload: dict) -> dict:
    """
    POST a JSON payload to the configured webhook URL.
    Signs the body with HMAC-SHA256 in X-Sentinel-Signature when
    WEBHOOK_SECRET is set (same verification pattern as GitHub webhooks).

    Never raises — a webhook delivery failure must never affect request
    processing. Returns {"sent": bool, "status_code": int | None, "error": str | None}.
    """
    if not settings.webhook_url:
        return {"sent": False, "status_code": None, "error": "no webhook_url configured"}

    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json"}
    signature = _sign(body)
    if signature:
        headers["X-Sentinel-Signature"] = signature

    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            resp = await client.post(settings.webhook_url, content=body, headers=headers)
        return {"sent": True, "status_code": resp.status_code, "error": None}
    except Exception as e:
        print(f"[Webhook] delivery to {settings.webhook_url} failed: {e}")
        return {"sent": False, "status_code": None, "error": str(e)}
