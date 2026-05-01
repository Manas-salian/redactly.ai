from celery import Celery

from app.config import get_settings

_s = get_settings()

celery_app = Celery(
    "redactly",
    broker=_s.broker_url,
    backend=_s.result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_time_limit=20 * 60,
    task_soft_time_limit=15 * 60,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
