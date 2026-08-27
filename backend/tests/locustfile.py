"""
Heavy load testing for SentinelAI's gateway. Three scenarios, run separately
(see README's "Load testing" section for exact commands):

  RealisticMixUser   — ~70% repeated / 30% unique prompts against the REAL
                        Groq/Gemini APIs. Validates the cache/cost value
                        proposition with real latency numbers. Keep
                        --users low (10-20) to stay under free-tier limits.

  InfraStressUser     — same traffic shape, but GROQ_BASE_URL/GEMINI_BASE_URL
                        point at tests/mock_provider.py (see
                        docker-compose.loadtest.yml). Nothing external
                        throttles this path, so it's safe to ramp to
                        hundreds of concurrent users — this is what finds
                        the gateway's own ceiling (DB pool, Redis
                        connections), not Groq's.

  Run the failover scenario as a script, not a Locust user class — see
  scripts/loadtest_failover.py, which needs to control the mock provider's
  failure rate mid-run in a way Locust's user model doesn't fit well.

Usage:
    locust -f tests/locustfile.py RealisticMixUser --host http://localhost:8000
    locust -f tests/locustfile.py InfraStressUser  --host http://localhost:8000 -u 200 -r 20
"""
import os
import random
import uuid

from locust import HttpUser, between, task

API_KEY = os.environ.get("LOADTEST_API_KEY", "sentinel-dev-key-123")
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

REPEATED_PROMPTS = [
    "What is PostgreSQL?",
    "Explain how Redis works.",
    "What is a circuit breaker pattern?",
    "Summarize what pgvector does.",
    "What is the difference between SQL and NoSQL?",
]


def _prompt(unique: bool) -> str:
    if unique:
        return f"Tell me one fact about the number {random.randint(1, 10_000_000)} — id {uuid.uuid4()}"
    return random.choice(REPEATED_PROMPTS)


class _ChatUserBase(HttpUser):
    abstract = True
    unique_ratio = 0.3

    @task
    def chat(self):
        body = {
            "messages": [{"role": "user", "content": _prompt(random.random() < self.unique_ratio)}],
            "max_tokens": 100,
        }
        with self.client.post("/v1/chat", headers=HEADERS, json=body, catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code} body={resp.text[:200]}")


class RealisticMixUser(_ChatUserBase):
    """Real providers — keep concurrency low (free-tier rate limits apply)."""
    wait_time = between(0.5, 2.0)
    unique_ratio = 0.3


class InfraStressUser(_ChatUserBase):
    """Mock provider — safe to ramp hard; this is testing the gateway itself."""
    wait_time = between(0.05, 0.3)
    unique_ratio = 0.3
