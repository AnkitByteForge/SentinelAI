#!/usr/bin/env python3
"""
SentinelAI bootstrap script.

Brings up Postgres + Redis, applies database migrations, and mints a
default API key so the stack is usable end to end in one command.

Run from the repo root, with the backend's Python environment active
(the one with fastapi/sqlalchemy/alembic installed — e.g. backend/venv):

    python scripts/bootstrap.py

What it does, in order:
  1. Checks Docker is running.
  2. Checks backend/.env exists — creates it from .env.example if not.
  3. Starts Postgres + Redis via docker-compose (infrastructure only).
  4. Waits for Postgres to report healthy.
  5. Runs `alembic upgrade head` to create the api_keys table.
  6. Creates one default API key and prints it — shown once.
  7. Prints a summary of what's running and how to start the rest.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

POSTGRES_WAIT_MAX_SECONDS = 30
POSTGRES_WAIT_POLL_SECONDS = 2


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def check_docker_running() -> None:
    print("\n[1/7] Checking Docker is running...")
    result = subprocess.run(["docker", "ps"], capture_output=True, text=True)
    if result.returncode != 0:
        print("Docker does not appear to be running.")
        print("Start Docker Desktop and re-run: python scripts/bootstrap.py")
        sys.exit(1)
    print("Docker is running.")


def ensure_env_file() -> None:
    print("\n[2/7] Checking backend/.env...")
    env_path = BACKEND_DIR / ".env"
    example_path = BACKEND_DIR / ".env.example"

    if env_path.exists():
        print("backend/.env already exists — leaving it as is.")
        return

    env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    print("Created backend/.env from backend/.env.example.")
    print(
        "IMPORTANT: edit backend/.env and set GROQ_API_KEY and GEMINI_API_KEY "
        "before sending real traffic — the gateway can't call either provider without them."
    )


def start_infra() -> None:
    print("\n[3/7] Starting Postgres and Redis (docker-compose up -d postgres redis)...")
    _run(["docker-compose", "up", "-d", "postgres", "redis"], cwd=REPO_ROOT)


def wait_for_postgres() -> None:
    print(f"\n[4/7] Waiting for Postgres to be ready (up to {POSTGRES_WAIT_MAX_SECONDS}s)...")
    deadline = time.monotonic() + POSTGRES_WAIT_MAX_SECONDS
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker-compose", "exec", "-T", "postgres", "pg_isready", "-U", "sentinel", "-d", "sentinelai"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("Postgres is ready.")
            return
        time.sleep(POSTGRES_WAIT_POLL_SECONDS)

    print(f"Postgres did not become ready within {POSTGRES_WAIT_MAX_SECONDS}s.")
    print("Check its logs with: docker-compose logs postgres")
    sys.exit(1)


def run_migrations() -> None:
    print("\n[5/7] Running alembic upgrade head...")
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR)


def create_default_key() -> str:
    print("\n[6/7] Creating a default API key...")
    sys.path.insert(0, str(BACKEND_DIR))
    import asyncio

    async def _create() -> str:
        from app.db.database import AsyncSessionLocal
        from app.services.api_keys import create_key

        async with AsyncSessionLocal() as db:
            _row, raw_key = await create_key(db=db, name="default", rate_limit=100)
            return raw_key

    return asyncio.run(_create())


def print_summary(default_key: str) -> None:
    print(f"""
[7/7] Done.

SentinelAI ready.
  Dashboard:  http://localhost:3000
  API:        http://localhost:8000/docs
  Admin key:  {default_key} (save this — shown once)

  Start everything: docker-compose up -d
  View logs:        docker-compose logs -f

Note: the key above is a per-tenant key with a default rate limit, for
calling /v1/chat. The separate master key in backend/.env (API_KEY) is
required for /v1/keys management endpoints — see README.md.
""")


def main() -> None:
    check_docker_running()
    ensure_env_file()
    start_infra()
    wait_for_postgres()
    run_migrations()
    default_key = create_default_key()
    print_summary(default_key)


if __name__ == "__main__":
    main()
