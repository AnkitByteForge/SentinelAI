"""
Load test for SentinelAI's /v1/chat gateway.

Fires a mix of repeated prompts (from a small fixed pool, to exercise the
semantic cache) and unique prompts (forced cache misses), then prints the
metrics needed for the README benchmark table: cache hit rate, avg latency
(hit vs. miss), latency reduction, cost saved (diffed from
/v1/cache/stats before/after), and p95/p99 latency at the given concurrency.

Usage:
    cd backend
    python tests/load_test.py --api-key sentinel-dev-key-123 --requests 500 --concurrency 20

Use the MASTER key (API_KEY in .env), not a per-tenant key, unless you've
raised that key's rate_limit above --requests — per-tenant keys are
rate-limited to 100 req/min by default and a burst load test will trip
that limit by design (which is itself worth demonstrating separately).

To measure "failures during simulated provider outage": temporarily set an
invalid GROQ_API_KEY in backend/.env, restart the backend, rerun this
script with --requests 20 --unique-ratio 1.0, and confirm 0 failures
(Gemini serves everything) — then check GET /v1/circuit/states for
groq: "open".
"""
import argparse
import asyncio
import random
import statistics
import time
import uuid

import httpx

REPEATED_PROMPTS = [
    "What is PostgreSQL?",
    "Explain how Redis works.",
    "What is a circuit breaker pattern?",
    "Summarize what pgvector does.",
    "What is the difference between SQL and NoSQL?",
]


def _make_prompt(unique: bool) -> str:
    if unique:
        return f"Tell me one fact about the number {random.randint(1, 10_000_000)} — id {uuid.uuid4()}"
    return random.choice(REPEATED_PROMPTS)


def _percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = min(int(len(s) * pct / 100), len(s) - 1)
    return s[idx]


async def _fire_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, host: str, headers: dict, unique: bool) -> dict:
    prompt = _make_prompt(unique)
    start = time.monotonic()
    async with sem:
        try:
            resp = await client.post(
                f"{host}/v1/chat",
                headers=headers,
                json={"messages": [{"role": "user", "content": prompt}], "max_tokens": 100},
                timeout=30.0,
            )
        except Exception as e:
            return {"ok": False, "latency_ms": (time.monotonic() - start) * 1000, "error": str(e)}

    latency_ms = (time.monotonic() - start) * 1000
    if resp.status_code != 200:
        return {"ok": False, "latency_ms": latency_ms, "status_code": resp.status_code, "body": resp.text[:200]}

    data = resp.json()
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "cache_hit": data["meta"]["cache_hit"],
        "cost_usd": data["usage"]["cost_usd"],
    }


async def run(host: str, api_key: str, total_requests: int, concurrency: int, unique_ratio: float) -> None:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        # Snapshot cache stats before the run for an accurate "cost saved" delta.
        before = (await client.get(f"{host}/v1/cache/stats", headers=headers)).json()

        tasks = [
            _fire_one(client, sem, host, headers, random.random() < unique_ratio)
            for _ in range(total_requests)
        ]
        wall_start = time.monotonic()
        results = await asyncio.gather(*tasks)
        wall_elapsed = time.monotonic() - wall_start

        after = (await client.get(f"{host}/v1/cache/stats", headers=headers)).json()

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    hits = [r for r in ok if r["cache_hit"]]
    misses = [r for r in ok if not r["cache_hit"]]

    hit_lat = [r["latency_ms"] for r in hits]
    miss_lat = [r["latency_ms"] for r in misses]
    all_lat = [r["latency_ms"] for r in ok]

    hit_avg = statistics.mean(hit_lat) if hit_lat else 0.0
    miss_avg = statistics.mean(miss_lat) if miss_lat else 0.0
    reduction_pct = ((miss_avg - hit_avg) / miss_avg * 100) if miss_avg else 0.0

    total_cost = sum(r["cost_usd"] for r in ok)
    cost_saved_delta = after.get("total_saved_usd", 0) - before.get("total_saved_usd", 0)
    hits_delta = after.get("total_hits", 0) - before.get("total_hits", 0)

    print(f"\n{'=' * 60}\nSentinelAI load test results\n{'=' * 60}")
    print(f"Requests:               {total_requests} ({len(ok)} ok, {len(failed)} failed)")
    print(f"Concurrency:            {concurrency}")
    print(f"Wall time:              {wall_elapsed:.2f}s ({total_requests / wall_elapsed:.1f} req/s)")
    print(f"Cache hit rate:         {len(hits) / len(ok) * 100:.1f}%" if ok else "Cache hit rate:         n/a")
    print(f"Avg latency (hit):      {hit_avg:.1f} ms  (n={len(hit_lat)})")
    print(f"Avg latency (miss):     {miss_avg:.1f} ms  (n={len(miss_lat)})")
    print(f"Latency reduction:      {reduction_pct:.1f}%")
    print(f"p95 latency (overall):  {_percentile(all_lat, 95):.1f} ms")
    print(f"p99 latency (overall):  {_percentile(all_lat, 99):.1f} ms")
    print(f"Total cost paid:        ${total_cost:.6f}")
    print(f"Cost saved (this run):  ${cost_saved_delta:.6f}  ({hits_delta} new cache hits recorded)")
    print(f"Failures:               {len(failed)}")
    for r in failed[:5]:
        print(f"  - {r}")
    print(f"{'=' * 60}\n")
    print("Fill these into README.md's benchmark table. For 'failures during simulated")
    print("provider outage': set an invalid GROQ_API_KEY, restart the backend, rerun with")
    print("--requests 20 --unique-ratio 1.0, confirm 0 failures, then check")
    print("GET /v1/circuit/states for groq: \"open\".\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test SentinelAI's /v1/chat gateway")
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True, help="Use the master API_KEY for an unthrottled run")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--unique-ratio", type=float, default=0.3,
                         help="Fraction of requests that are unique prompts (forces a cache miss)")
    args = parser.parse_args()
    asyncio.run(run(args.host, args.api_key, args.requests, args.concurrency, args.unique_ratio))


if __name__ == "__main__":
    main()
