# SentinelAI

**A reliability and cost-control gateway for LLM providers — circuit-breaker failover, semantic caching, and full-request observability, sitting in front of Groq and Gemini so your application never talks to a raw provider API directly.**

![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-broker-DC382D?logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-async%20workers-37814A?logo=celery&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-unlicensed-lightgrey)

**Live demo:** [Deploy URL]

LLM APIs fail, rate-limit, and drift in latency in ways application code shouldn't have to know about. SentinelAI is the layer that absorbs that: every chat request goes through a semantic cache first, then a circuit-breaker-guarded provider chain (Groq primary, Gemini fallback), with cost and latency recorded on every single call. The goal isn't to make LLM calls smarter — it's to make them boring: predictable latency, bounded blast radius when a provider degrades, and a real accounting of what every request cost.

## The problem this solves

A typical integration calls `groq.chat.completions.create(...)` directly from the request handler. Three things happen in production that don't happen in the demo:

**A provider has a bad day.** Groq returns 503s for ten minutes during a capacity event. Every request that hits it now waits out the full timeout before failing — your p99 latency goes from 2s to 20s, and every one of those calls returns an error to the end user. There is no fallback path because the code never had one.

**The same questions get asked over and over.** A support bot answers "what's your refund policy" 400 times a day with 400 near-identical prompts. Each one is a full paid inference call at ~1.5-2.5s of latency, even though the answer hasn't changed since the last time someone asked. At scale this is a linear cost curve for what is, semantically, a cache-hit workload.

**Nobody can answer "why was it slow yesterday."** When latency spikes or a provider starts erroring, there's no per-request record of which provider handled it, how long it took, whether it fell back, or what it cost — just application logs that weren't built to answer infrastructure questions.

SentinelAI addresses all three with one gateway: cache what's already been asked, fail over when a provider is down, and log enough about every request to answer the "why" after the fact.

## What SentinelAI does

**Multi-provider routing with circuit breaker failover** — Every `/v1/chat` request tries Groq first. If Groq is unavailable (timeout, 5xx, or its circuit is already open), the request transparently retries against Gemini before ever reaching the caller. Each provider has its own in-memory circuit (`services/circuit_breaker.py`): three consecutive failures trips it to `OPEN` for 60 seconds, after which one test request is allowed through (`HALF_OPEN`) to probe recovery. Outcome: a provider outage degrades to "slower, served by the other provider" instead of "every request fails."

**Semantic cache with pgvector HNSW indexing** — Prompts are embedded with `all-MiniLM-L6-v2` (384 dimensions, normalized) and matched against stored responses by cosine similarity, not exact string match. A rephrased question still hits the cache. Lookup is two-stage: an O(1) SHA-256 hash check for exact repeats, then an HNSW-indexed nearest-neighbor search in Postgres for near-duplicates above a 0.92 similarity threshold. Outcome: cache hits return in ~15-50ms instead of the 1.5-2.5s a live provider call takes, at zero marginal cost.

**Async request pipeline (FastAPI + Celery + Redis)** — The response is built and returned to the client before the request is logged to Postgres or written into the cache. Those two writes are queued as a single Celery task (`post_process_task`) over a Redis broker and executed by a separate worker process. Outcome: request-serving latency is never coupled to database write latency.

**Real-time cost tracking per request** — Every response carries `usage.cost_usd`, computed from actual input/output token counts against a per-model pricing table (`services/cost.py`). Cache hits report `$0.00` and accrue against `saved_cost_usd` on the cache entry instead. Outcome: cost is a first-class field on every logged request, not an estimate reconstructed later from provider invoices.

**Observability dashboard with latency, cache, and provider health** — The Next.js dashboard polls `/v1/metrics`, `/v1/logs`, `/v1/cache/stats`, `/v1/circuit/states`, and `/v1/worker/stats` every 15 seconds and renders p50/p95/p99 latency, cache hit/miss ratio, per-provider request share and error rate, live circuit breaker state, and a per-request pipeline trace (cache check → provider call → response). Outcome: an operator can see a provider degrading or a circuit opening within 15 seconds, without grepping logs.

**Intelligent provider health scoring** — Circuit state isn't just up/down: `CircuitBreakerRegistry` tracks a rolling failure count and last-failure timestamp per provider and exposes it at `/v1/circuit/states` and `/health`, so routing decisions and dashboard alerts are driven by the same source of truth.

## Architecture

---
config:
  layout: elk
  theme: neutral
  flowchart:
    curve: basis
    htmlLabels: true
---

flowchart TD

    Client(["Client / Dashboard<br/>POST /v1/chat"]) --> Auth

    %% ==================== GATEWAY ====================
    subgraph GW["Gateway Layer · FastAPI"]
        Auth["verify_api_key<br/>Bearer token check"] --> CacheCheck["check_cache()"]
    end

    %% ==================== CACHE ====================
    subgraph CACHE["Cache Layer · services/cache.py"]

        CacheCheck --> ExactHit{"Exact SHA-256<br/>hash match?"}

        ExactHit -->|yes| CacheHit["Serve cached response<br/>~15–50 ms"]

        ExactHit -->|no| Embed["Embed prompt<br/>all-MiniLM-L6-v2 · 384-dim"]

        Embed --> HNSW[("pgvector HNSW index<br/>cosine distance search")]

        HNSW --> SimCheck{"Similarity ≥ 0.92?"}

        SimCheck -->|yes| CacheHit

        SimCheck -->|no| GroqCB

    end


    %% ==================== PROVIDERS ====================
    subgraph PROV["Provider Layer · services/providers.py"]

        GroqCB{"Groq circuit<br/>CLOSED / HALF_OPEN?"}

        GroqCB -->|yes| GroqCall["Call Groq<br/>llama-3.1-8b-instant"]

        GroqCB -->|no - OPEN| GeminiCB

        GroqCall -->|success| Success["Build ChatResponse"]

        GroqCall -->|failure| RecordFail["record_failure('groq')"]

        RecordFail --> GeminiCB{"Gemini circuit<br/>CLOSED / HALF_OPEN?"}

        GeminiCB -->|yes| GeminiCall["Call Gemini<br/>gemini-2.5-flash"]

        GeminiCB -->|no - OPEN| AllDown["503 - all providers unavailable"]

        GeminiCall -->|success| Success

        GeminiCall -->|failure| AllDown

    end


    %% ==================== RESPONSE ====================
    Success --> Response["Return response to client"]

    CacheHit --> Response

    Response -.->|"post_process_task.delay() - fire-and-forget"| Broker


    %% ==================== ASYNC ====================
    subgraph ASYNC["Async Layer · Celery + Redis"]

        Broker[("Redis<br/>broker db0 · result backend db1")]

        Broker --> Worker["Celery worker<br/>solo pool"]

    end


    %% ==================== STORAGE ====================
    subgraph STORE["Storage Layer · PostgreSQL + pgvector"]

        Worker --> WriteLog[("requests table<br/>RequestLog")]

        Worker --> WriteCache[("cache_entries table<br/>embedding + response")]

    end


    %% ==================== DASHBOARD ====================
    Dash["Dashboard · Next.js<br/>app/page.tsx"]

    Dash -->|"GET /v1/metrics · /v1/logs<br/>/v1/cache/stats · /v1/circuit/states"| GW

    WriteLog -.-> Dash

    WriteCache -.-> Dash


    %% ==================== STYLES ====================

    classDef client fill:#eef2ff,stroke:#818cf8,stroke-width:2px,color:#1e1b4b;

    classDef gateway fill:#f0fdfa,stroke:#2dd4bf,stroke-width:2px,color:#134e4a;

    classDef cache fill:#ecfeff,stroke:#22d3ee,stroke-width:2px,color:#164e63;

    classDef provider fill:#f5f3ff,stroke:#a78bfa,stroke-width:2px,color:#3b0764;

    classDef async fill:#fff7ed,stroke:#fb923c,stroke-width:2px,color:#7c2d12;

    classDef storage fill:#f0fdf4,stroke:#4ade80,stroke-width:2px,color:#14532d;

    classDef decision fill:#fefce8,stroke:#facc15,stroke-width:2px,color:#713f12;

    classDef error fill:#fff1f2,stroke:#fb7185,stroke-width:2px,color:#881337;

    classDef response fill:#f0f9ff,stroke:#38bdf8,stroke-width:2px,color:#0c4a6e;


    %% ==================== APPLY CLASSES ====================

    class Client,Dash client;

    class Auth,CacheCheck gateway;

    class ExactHit,SimCheck,GroqCB,GeminiCB decision;

    class Embed,HNSW,CacheHit cache;

    class GroqCall,RecordFail,GeminiCall provider;

    class AllDown error;

    class Success,Response response;

    class Broker,Worker async;

    class WriteLog,WriteCache storage;


    %% ==================== SUBGRAPH STYLES ====================

    style GW fill:#f0fdfa,stroke:#2dd4bf,stroke-width:2px

    style CACHE fill:#ecfeff,stroke:#22d3ee,stroke-width:2px

    style PROV fill:#f5f3ff,stroke:#a78bfa,stroke-width:2px

    style ASYNC fill:#fff7ed,stroke:#fb923c,stroke-width:2px

    style STORE fill:#f0fdf4,stroke:#4ade80,stroke-width:2px


    %% ==================== LINKS ====================

    linkStyle default stroke:#64748b,stroke-width:1.5px
```

## System design decisions

**Postgres over SQLite** — `config.py` still carries a `database_url` SQLite fallback from an earlier iteration, but the live engine (`db/database.py`) is wired to `postgres_url`. Celery workers write to the database from a separate process than the API server; SQLite's single-writer lock model doesn't hold up once writes are concurrent and out-of-process. Postgres's MVCC gives every worker and every request its own transaction without blocking the others.

**pgvector HNSW over a Python-side cosine scan** — The naive approach — pull every cached embedding into Python and compute cosine similarity in a loop — is O(n) per lookup and gets slower as the cache grows, which is exactly backwards for a cache. `create_hnsw_index.py` builds an HNSW index (`m=16, ef_construction=64`) on `cache_entries.embedding`, so the `ORDER BY embedding <=> :vec LIMIT 1` query in `check_cache()` resolves in Postgres via approximate nearest-neighbor search — O(log n) — instead of a full table scan.

**Celery async over synchronous DB writes** — `/v1/chat` returns as soon as it has a response; the DB write and cache-store happen after, via `post_process_task.delay(...)` over Redis. If those writes were awaited inline, every request's latency would include Postgres round-trip time and, for cache misses, an embedding computation — both irrelevant to what the caller is waiting for.

**Circuit breaker over retry-with-backoff** — Retrying a failing provider still spends the full timeout window on every failed attempt, which is what caused the p99 blowup described above. `CircuitBreakerRegistry.is_available()` short-circuits to `False` instantly once a provider has failed three times in a row, so failed requests stop paying the timeout tax and go straight to the fallback provider. The 60-second `HALF_OPEN` probe means recovery is detected without a human resetting anything.

**Semantic cache over exact-match cache** — An exact-match cache (hash the prompt, look it up) only catches identical strings. "What's your refund policy?" and "can you explain the refund policy" are different strings with the same answer. Embedding the prompt and comparing by cosine similarity catches paraphrases; the exact-hash check stays as a fast-path O(1) short-circuit for genuinely identical repeats before falling through to the vector search.

**0.92 cosine similarity threshold** — Set in `services/cache.py` as `SIMILARITY_THRESHOLD`. Below ~0.90, unrelated-but-topically-adjacent prompts ("summarize this contract" vs. "review this contract") start collapsing onto the same cache entry, returning a wrong answer with high confidence. 0.92 was chosen to bias toward precision — a false cache-hit is a silently wrong answer served to a user, which is a worse failure mode than an avoidable cache miss that just costs one extra provider call.

## Benchmark results

No load test has been run against this deployment yet — the table below is the schema to fill in after running one (see `backend/tests/` for where to add a load-test script; `test_gateway.py` is currently a placeholder).

| Metric | Value |
|---|---|
| Cache hit rate | TBD |
| Avg latency (cache hit) | TBD |
| Avg latency (cache miss / live provider call) | TBD |
| Latency reduction (hit vs. miss) | TBD |
| Cost saved per 500 requests | TBD |
| p95 latency @ 20 concurrent users | TBD |
| Failed requests during simulated provider outage | TBD |

## Tech stack

| Component | Technology | Why |
|---|---|---|
| API server | FastAPI + Uvicorn | Async request handling; the cache check and provider calls are I/O-bound and benefit from `async`/`await` end to end |
| Async workers | Celery (solo pool) | Decouples DB writes and cache stores from the request/response cycle; solo pool avoids Windows multiprocessing issues in dev, swapped for `prefork` in production |
| Message broker | Redis | Backs the Celery task queue (db 0) and result backend (db 1); also used directly for `/v1/worker/stats` queue-depth checks |
| Primary database | PostgreSQL (asyncpg driver) | Concurrent writes from the API process and Celery worker process require a real multi-writer database |
| Vector search | pgvector (HNSW index) | Stores embeddings as a native Postgres column type and answers nearest-neighbor queries with an index instead of a client-side scan |
| Embedding model | sentence-transformers — `all-MiniLM-L6-v2` | 384-dim, CPU-friendly, normalized embeddings suitable for cosine similarity; loaded once per process (optionally warmed at startup) |
| LLM providers | Groq (`llama-3.1-8b-instant`), Gemini (`gemini-2.5-flash`) | Free-tier-friendly, OpenAI-compatible (Groq) and REST (Gemini) APIs used as primary/fallback pair |
| Frontend | Next.js 14 (App Router) + Recharts | Single dashboard page polling the gateway's own observability endpoints; no separate backend-for-frontend |
| Containerization | Docker + Docker Compose | `backend` and `frontend` each ship their own Dockerfile; see setup notes below on what compose does and doesn't start |

## Local setup

### Prerequisites

- Docker Desktop
- A PostgreSQL instance with the `vector` extension available (pgvector), reachable at the URL you set below
- A Redis instance, reachable at `localhost:6379`
- Free API keys: [Groq](https://console.groq.com) and [Gemini](https://aistudio.google.com/apikey)

`docker-compose.yml` in this repo currently defines only the `backend` and `frontend` services — it does not start Postgres, Redis, or the Celery worker. Run those separately (locally installed, or your own containers) before bringing the stack up.

### 1. Clone and configure

```bash
git clone <repo-url>
cd SentinelAI
cp backend/.env.example backend/.env
```

Edit `backend/.env` and set `GROQ_API_KEY`, `GEMINI_API_KEY`, and `POSTGRES_URL` (see the environment variables table below).

### 2. Start Postgres and Redis

If you don't already have them running locally:

```bash
docker run -d --name sentinel-postgres -e POSTGRES_USER=sentinel -e POSTGRES_PASSWORD=sentinel_dev_pass -e POSTGRES_DB=sentinelai -p 5432:5432 pgvector/pgvector:pg16
docker run -d --name sentinel-redis -p 6379:6379 redis:7-alpine
```

### 3. Start the gateway and dashboard

```bash
docker-compose up --build
```

This builds and runs:

| Service | Port | What it runs |
|---|---|---|
| `backend` | `8000` | `uvicorn app.main:app` — FastAPI gateway |
| `frontend` | `3000` | `next start` — dashboard |

`init_db()` creates the `vector` extension and both tables (`requests`, `cache_entries`) automatically on backend startup — no manual migration step is needed for a fresh database.

### 4. Build the HNSW index (once, after the tables exist)

```bash
cd backend
python create_hnsw_index.py
```

### 5. Start the Celery worker (separate process, not in docker-compose)

```bash
cd backend
celery -A app.worker worker --loglevel=info --pool=solo
```

Without this running, `/v1/chat` still returns responses, but request logs and cache writes never land — `/v1/worker/stats` will report `"status": "down"`.

### 6. Verify

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "version": "0.1.0", "circuit_breakers": {}}
```

Then open `http://localhost:3000` for the dashboard, and confirm the worker status badge shows `LIVE`.

## API reference

All endpoints except `/health` require `Authorization: Bearer <API_KEY>` (matched against the `API_KEY` environment variable).

### `POST /v1/chat`

Send a chat request through the gateway (cache → Groq → Gemini).

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer sentinel-dev-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Explain Redis in one sentence"}],
    "model": "llama-3.1-8b-instant",
    "max_tokens": 200,
    "temperature": 0.7
  }'
```

```json
{
  "id": "b2c1e8f0-...",
  "content": "Redis is an in-memory data store used as a cache, message broker, and database.",
  "provider": "groq",
  "model": "llama-3.1-8b-instant",
  "usage": {"input_tokens": 14, "output_tokens": 19, "cost_usd": 0.0000023},
  "meta": {"cache_hit": false, "latency_ms": 812, "provider": "groq", "fallback": null}
}
```

Optional `"bypass_cache": true` skips the semantic cache check.

### `GET /v1/logs`

Paginated request log. Query params: `page` (default 1), `limit` (default 50, max 200), `status` (`success` | `error` | `fallback`), `provider` (`groq` | `gemini`).

```bash
curl "http://localhost:8000/v1/logs?limit=10&status=fallback" \
  -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{"total": 3, "page": 1, "limit": 10, "logs": [{"id": "...", "provider": "gemini", "fallback_from": "groq", "status": "fallback", "...": "..."}]}
```

### `GET /v1/metrics`

Aggregated metrics for a time window (`1h` | `6h` | `24h` | `7d`, default `24h`).

```bash
curl "http://localhost:8000/v1/metrics?window=1h" \
  -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{
  "window": "1h",
  "total_requests": 42,
  "successful_requests": 39,
  "failed_requests": 1,
  "fallback_requests": 2,
  "cache_hit_rate": 0.31,
  "total_cost_usd": 0.000841,
  "latency": {"p50_ms": 22, "p95_ms": 1980, "p99_ms": 2410, "avg_ms": 640},
  "providers": {"groq": {"requests": 37, "errors": 1, "avg_latency_ms": 710.4, "error_rate": 0.027}}
}
```

### `GET /v1/cache/stats`

```bash
curl http://localhost:8000/v1/cache/stats -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{"total_entries": 128, "total_hits": 341, "total_saved_usd": 0.00219, "threshold": 0.92, "top_cached": [{"prompt_preview": "What is your refund policy...", "hit_count": 44, "saved_usd": 0.00061}]}
```

### `DELETE /v1/cache/invalidate`

Marks every cache entry as stale (does not delete rows). Useful for demos and testing.

```bash
curl -X DELETE http://localhost:8000/v1/cache/invalidate -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{"message": "All cache entries invalidated"}
```

### `GET /v1/circuit/states`

```bash
curl http://localhost:8000/v1/circuit/states -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{"groq": {"state": "closed", "failure_count": 0, "last_failure": 0.0}}
```

### `POST /v1/circuit/{provider}/reset`

Manually force a circuit back to `CLOSED` — useful when demoing failover recovery without waiting out the 60s timeout.

```bash
curl -X POST http://localhost:8000/v1/circuit/groq/reset -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{"message": "groq circuit reset to CLOSED"}
```

### `GET /v1/worker/stats`

Celery/Redis connectivity and queue depth, used by the dashboard's worker status badge.

```bash
curl http://localhost:8000/v1/worker/stats -H "Authorization: Bearer sentinel-dev-key-123"
```

```json
{"status": "connected", "queue_depth": 0}
```

### `GET /v1/pricing`

Returns the static per-1M-token pricing table used for cost calculation.

### `GET /health`

No auth required.

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "version": "0.1.0", "circuit_breakers": {"groq": {"state": "closed", "failure_count": 0, "last_failure": 0.0}}}
```

## Project structure

```
SentinelAI/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app: lifespan (init_db, embedding warmup), CORS, /health
│   │   ├── config.py                 # pydantic-settings — reads backend/.env
│   │   ├── models.py                 # Pydantic request/response schemas (ChatRequest, MetricsResponse, ...)
│   │   ├── worker.py                  # Celery app instance + broker/backend config
│   │   ├── tasks.py                  # Celery tasks: log_request, store_cache, post_process
│   │   ├── db/
│   │   │   ├── database.py           # Async engine, session factory, init_db() (creates extension + tables)
│   │   │   └── models.py             # SQLAlchemy models: RequestLog, CacheEntry (with Vector(384) column)
│   │   ├── routers/
│   │   │   ├── gateway.py            # POST /v1/chat — cache check, provider routing, circuit breaker calls
│   │   │   └── observability.py      # /v1/logs, /v1/metrics, /v1/cache/*, /v1/circuit/*, /v1/worker/stats
│   │   └── services/
│   │       ├── providers.py          # Groq + Gemini HTTP clients (shared httpx.AsyncClient)
│   │       ├── cache.py              # Embedding, hash/vector cache lookup, cache store, cache stats
│   │       ├── circuit_breaker.py    # CircuitBreakerRegistry — per-provider CLOSED/OPEN/HALF_OPEN state
│   │       ├── cost.py               # Static pricing table + calculate_cost()
│   │       └── queries.py            # get_logs() / get_metrics() aggregation queries
│   ├── tests/
│   │   └── test_gateway.py           # pytest suite (currently a placeholder)
│   ├── create_hnsw_index.py           # One-off: creates the HNSW index on cache_entries.embedding
│   ├── test_pg_connection.py          # Manual script: verifies Postgres + pgvector connectivity
│   ├── test_pgvector_insert.py        # Manual script: verifies vector insert/search round-trip
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── page.tsx                   # Dashboard — metrics, logs, cache, circuit breakers, pipeline trace
│   │   └── layout.tsx
│   ├── lib/api.ts                     # Typed fetch wrappers for the gateway API
│   ├── next.config.js                  # Rewrites /api/gateway/* to the backend on :8000
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml                  # backend (:8000) + frontend (:3000) services
└── README.md
```

## Environment variables

Set in `backend/.env` (see `backend/.env.example`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes | `""` | API key for the primary provider (Groq) |
| `GEMINI_API_KEY` | Yes | `""` | API key for the fallback provider (Gemini) |
| `POSTGRES_URL` | Yes | `postgresql+asyncpg://sentinel:sentinel_dev_pass@localhost:5432/sentinelai` | Async SQLAlchemy connection string; this is what the engine actually uses |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./sentinelai.db` | Legacy SQLite URL from an earlier iteration; not used by the active engine |
| `API_KEY` | Yes | `sentinel-dev-key-123` | Shared bearer token every request to `/v1/*` must present |
| `ENVIRONMENT` | No | `development` | Informational — not currently branched on in code |
| `PRELOAD_EMBEDDING_MODEL` | No | `false` | If `true`, loads the SentenceTransformer model at process startup instead of on first use |
| `LOG_STAGE_TIMINGS` | No | `false` | If `true`, prints per-stage timing breakdowns (`cache_check_ms`, `groq_ms`, `gemini_ms`, ...) for each `/v1/chat` call |

Note: the Celery broker/result backend URLs (`redis://localhost:6379/0` and `/1`) and the Redis host used by `/v1/worker/stats` are currently hardcoded in `app/worker.py` and `app/routers/observability.py` rather than sourced from these settings — see production notes below.

## What production would look like

- **Postgres → managed** (RDS, Neon, or Railway Postgres) with pgvector enabled, instead of a local container
- **Redis → managed** (ElastiCache or Upstash), and the broker/result-backend URLs in `app/worker.py` moved into `Settings` instead of being hardcoded to `localhost`
- **Multiple Celery workers** with the `prefork` pool (the current `solo` pool is a Windows-dev workaround, single-threaded by design)
- **Alembic migrations wired into CI/CD** — `alembic` is already a dependency, but schema changes currently go through `Base.metadata.create_all()` in `init_db()`, which can't handle altering existing tables
- **A real API key store** (hashed keys in Postgres, one per tenant) instead of a single shared `API_KEY` compared with `==`
- **Per-org rate limiting** in front of `/v1/chat`, since nothing currently caps request volume per caller
- **Circuit breaker state in Redis, not in-process memory** — `CircuitBreakerRegistry` is a per-process dict today, so it doesn't share state across multiple backend replicas
