import structlog

from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    """Smoke task — used by ops to confirm the worker is alive.

    Real tasks (parse_job, detect_job, redact_job) land in Plans 2-4.
    """
    log.info("ping")
    return "pong"
