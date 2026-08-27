#!/usr/bin/env python3
"""
Automated failover scenario: forces the mock Groq provider into 100%
failure mid-run and asserts the gateway keeps serving every request via
Gemini with zero failures, then confirms the circuit breaker actually
opened.

Requires the gateway pointed at tests/mock_provider.py — see
docker-compose.loadtest.yml and the "Load testing" section in README.

Usage:
    python scripts/loadtest_failover.py --host http://localhost:8000 \
        --mock-host http://localhost:9000 --api-key sentinel-dev-key-123
"""
import argparse
import sys
import time
import uuid

import httpx


def _chat(client: httpx.Client, host: str, headers: dict) -> httpx.Response:
    return client.post(
        f"{host}/v1/chat",
        headers=headers,
        json={"messages": [{"role": "user", "content": f"failover test {uuid.uuid4()}"}], "max_tokens": 20},
        timeout=30.0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated circuit-breaker failover scenario")
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--mock-host", default="http://localhost:9000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--requests-per-phase", type=int, default=15)
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}", "Content-Type": "application/json"}
    client = httpx.Client()

    print("[1/4] Baseline: confirming both mock providers are healthy...")
    client.post(f"{args.mock_host}/_control/groq", params={"failure_rate": 0.0})
    client.post(f"{args.mock_host}/_control/gemini", params={"failure_rate": 0.0})
    client.post(f"{args.host}/v1/circuit/groq/reset", headers=headers)
    client.post(f"{args.host}/v1/circuit/gemini/reset", headers=headers)

    baseline_failures = sum(
        1 for _ in range(args.requests_per_phase) if _chat(client, args.host, headers).status_code != 200
    )
    print(f"    baseline failures: {baseline_failures}/{args.requests_per_phase}")

    print("[2/4] Forcing groq to fail 100% of requests...")
    client.post(f"{args.mock_host}/_control/groq", params={"failure_rate": 1.0})

    print(f"[3/4] Sending {args.requests_per_phase} requests during the 'outage'...")
    outage_failures = 0
    fallback_count = 0
    for _ in range(args.requests_per_phase):
        resp = _chat(client, args.host, headers)
        if resp.status_code != 200:
            outage_failures += 1
            continue
        if resp.json()["meta"].get("fallback") == "groq":
            fallback_count += 1
    print(f"    outage failures: {outage_failures}/{args.requests_per_phase} (want 0)")
    print(f"    served via gemini fallback: {fallback_count}/{args.requests_per_phase}")

    print("[4/4] Checking circuit breaker state...")
    states = client.get(f"{args.host}/v1/circuit/states", headers=headers).json()
    groq_state = states.get("groq", {}).get("state")
    print(f"    groq circuit state: {groq_state} (want 'open')")

    # Clean up — leave the mock provider and circuit in a healthy state.
    client.post(f"{args.mock_host}/_control/groq", params={"failure_rate": 0.0})
    client.post(f"{args.host}/v1/circuit/groq/reset", headers=headers)

    ok = outage_failures == 0 and groq_state == "open"
    print(f"\n{'PASSED' if ok else 'FAILED'}: zero failed requests during the outage "
          f"and groq's circuit opened as expected." if ok else
          f"\nFAILED: expected 0 outage failures and groq circuit 'open', "
          f"got {outage_failures} failures and state={groq_state}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
