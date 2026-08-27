# ── Builder: install dependencies into a venv, kept out of the runtime layer ──
FROM python:3.12-slim AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
# torch first, from the CPU-only wheel index — pulling it from the default
# index (as a transitive dep of sentence-transformers, via requirements.txt
# below) drags in ~1GB of unused NVIDIA/CUDA packages. This has to be a
# separate install, not a global --extra-index-url in requirements.txt:
# that makes pip consider *every* package from both indexes and can hit
# spurious hash mismatches where a package exists on both.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch==2.11.0+cpu
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime: slim image, non-root user ──────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

RUN groupadd --gid 1000 sentinel \
    && useradd --uid 1000 --gid sentinel --create-home --shell /bin/bash sentinel

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY --chown=sentinel:sentinel . .

USER sentinel

# Backend and worker share this image with different `command:` overrides
# in docker-compose.yml (worker runs Celery, not uvicorn) — health checks
# for each are defined there instead of here, since they need different
# checks and this Dockerfile has no way to know which one it'll run as.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
