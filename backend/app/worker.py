from celery import Celery
from celery.signals import worker_init

from app.config import settings

# ── Celery app instance ───────────────────────────────────────────────
# broker  = where tasks are queued (Redis)
# backend = where task results are stored (Redis)
# We don't need task results for logging, but setting it keeps Celery happy

celery_app = Celery(
    "sentinelai",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
    include=["app.tasks"],          # where to find task definitions
)

celery_app.conf.update(
    task_serializer       = "json",
    accept_content        = ["json"],
    result_serializer     = "json",
    timezone              = "UTC",
    enable_utc            = True,
    task_track_started    = True,

    # Important for Windows — solo pool avoids multiprocessing issues
    # On Linux in production you'd remove this and use prefork
    worker_pool           = "solo",

    # Don't store results — we don't need them for fire-and-forget logging
    task_ignore_result    = True,

    # Retry failed tasks up to 3 times with 5s delay
    task_acks_late        = True,
    task_reject_on_worker_lost = True,
)


@worker_init.connect
def _warmup_embeddings_on_worker_init(**_kwargs):
    """Optional: preload SentenceTransformer inside the Celery worker process."""
    if not settings.preload_embedding_model:
        return
    try:
        from app.services.cache import warmup_embedding_model

        warmup_embedding_model()
    except Exception as e:
        print(f"[Embeddings] Warmup failed: {e}")