"""
Shared pytest fixtures.

Order matters here: app.config.Settings() and app.db.database's engine are
both created eagerly at import time, so POSTGRES_URL (and the other env
vars below) must be set — and the test Postgres container must be running
— before any `app.*` module is imported anywhere in the test suite.
conftest.py is imported by pytest before test modules in the same
directory, so this module-level code (not a fixture) is what makes that
ordering guarantee hold.
"""
import asyncio
import atexit
import os

from testcontainers.postgres import PostgresContainer

_container = PostgresContainer(image="pgvector/pgvector:pg16")
_container.start()
atexit.register(_container.stop)

os.environ["POSTGRES_URL"] = (
    f"postgresql+asyncpg://{_container.username}:{_container.password}"
    f"@{_container.get_container_host_ip()}:{_container.get_exposed_port(5432)}/{_container.dbname}"
)
os.environ.setdefault("API_KEY", "test-master-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("WEBHOOK_URL", "")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from pathlib import Path  # noqa: E402

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_migrations() -> None:
    cfg = AlembicConfig(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    alembic_command.upgrade(cfg, "head")


_run_migrations()

# ── Everything below can safely import app.* now ───────────────────────
import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

import app.services.redis_client as redis_client_module  # noqa: E402
from app.db.database import AsyncSessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """
    app.db.database creates its async engine/connection pool once at
    import time, bound to whatever event loop was running then. With
    pytest-asyncio's default per-test event loop, a pooled connection from
    one test's loop is not valid on the next test's loop — asyncpg raises
    "Future attached to a different loop". Disposing the pool after every
    test forces the next one to open fresh connections under its own loop,
    which is simpler and more version-robust than trying to pin every test
    and fixture onto one shared session-scoped loop.
    """
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """
    Every service resolves Redis via app.services.redis_client.get_redis(),
    which caches a client in that module's `_redis` global. Setting that
    global directly (rather than patching get_redis itself) works no
    matter how a given module imported the function — see the module
    docstring in redis_client.py.
    """
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client_module, "_redis", fake)
    return fake


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Truncate app tables before each test so tests don't see each other's rows."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import text
        await db.execute(text("TRUNCATE requests, cache_entries, api_keys RESTART IDENTITY CASCADE"))
        await db.commit()
    yield


_pending_task_side_effects: list = []


@pytest.fixture(autouse=True)
def no_celery_delay(monkeypatch):
    """
    Integration tests exercise the HTTP layer, not a real Celery broker —
    .delay(...) would otherwise try to reach real Redis via kombu.

    Running the task synchronously in a worker thread was tried first and
    rejected: a separate thread means a separate event loop, and asyncpg
    connections from the shared engine's pool aren't safe to hand out
    across different loops even via a proper pool checkout — same
    underlying issue `_dispose_engine_between_tests` exists for, just
    triggered within a single test instead of between two.

    Instead, `.delay()` schedules the real task logic (app.tasks._post_process
    etc. — see tasks.py; these are the same functions a real worker calls,
    just not wrapped in Celery's own new-event-loop-per-call machinery) as
    an asyncio task on the test's own already-running loop, and the
    `client` fixture below drains all pending ones after every request.
    That ordering matters for tests like cache-hit-on-repeat, which need
    the first request's background write to have landed before the second
    request is sent.
    """
    from app.tasks import _post_process, _touch_api_key

    def fake_post_process_delay(**kwargs):
        _pending_task_side_effects.append(asyncio.ensure_future(_post_process(**kwargs)))

    def fake_touch_delay(**kwargs):
        _pending_task_side_effects.append(asyncio.ensure_future(_touch_api_key(**kwargs)))

    monkeypatch.setattr("app.routers.gateway.post_process_task.delay", fake_post_process_delay)
    monkeypatch.setattr("app.routers.gateway.touch_api_key_task.delay", fake_touch_delay)


@pytest.fixture(autouse=True)
def _reset_circuit_breaker_singleton():
    """
    services.circuit_breaker.registry is a module-level singleton (by
    design — see its docstring), so its in-memory fallback dict survives
    across tests even though fake_redis is fresh each time. Without this,
    a provider touched by an earlier test keeps showing up in
    get_all_states() for every test after it, via that fallback dict's
    keys — even when the (fresh, per-test) fake Redis has nothing for it.
    """
    from app.services.circuit_breaker import registry
    registry._local_fallback.clear()
    yield
    registry._local_fallback.clear()


@pytest_asyncio.fixture
async def client():
    """
    Drains _pending_task_side_effects (see no_celery_delay above) after
    every request, so a test's second request always sees the first
    request's "background" write already landed — matching what actually
    matters about fire-and-forget from a test's point of view, without
    needing a real Celery worker.
    """
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        original_request = c.request

        async def request_and_drain(*args, **kwargs):
            response = await original_request(*args, **kwargs)
            while _pending_task_side_effects:
                await _pending_task_side_effects.pop()
            return response

        c.request = request_and_drain
        yield c
    _pending_task_side_effects.clear()


@pytest.fixture
def master_headers():
    return {"Authorization": f"Bearer {os.environ['API_KEY']}"}


@pytest.fixture
def mock_providers(monkeypatch):
    """
    Controls how the two mocked provider calls behave for a test. Patches
    the names as imported into routers.gateway (not providers.py itself —
    `from x import y` binds a local name, so that's the reference that
    actually needs patching for routers.gateway to see the fake).
    """
    state = {"groq": "success", "gemini": "success"}
    calls = {"groq": 0, "gemini": 0}

    def _response(provider: str, model: str) -> dict:
        return {
            "content": f"mock {provider} response",
            "provider": provider,
            "model": model,
            "input_tokens": 10,
            "output_tokens": 5,
            "latency_ms": 1,
            "cost_usd": 0.0000001,
        }

    def _failure() -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://example.invalid")
        response = httpx.Response(503, request=request)
        return httpx.HTTPStatusError("mock provider failure", request=request, response=response)

    async def fake_call_groq(messages, model, max_tokens, temperature):
        calls["groq"] += 1
        if state["groq"] == "fail":
            raise _failure()
        return _response("groq", model)

    async def fake_call_gemini(messages, max_tokens, temperature):
        calls["gemini"] += 1
        if state["gemini"] == "fail":
            raise _failure()
        return _response("gemini", "gemini-2.5-flash")

    monkeypatch.setattr("app.routers.gateway.call_groq", fake_call_groq)
    monkeypatch.setattr("app.routers.gateway.call_gemini", fake_call_gemini)

    class Controller:
        def set(self, provider: str, mode: str) -> None:
            assert mode in ("success", "fail")
            state[provider] = mode

        @property
        def calls(self) -> dict:
            return calls

    return Controller()
