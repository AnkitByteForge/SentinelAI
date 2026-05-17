from celery import Celery

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