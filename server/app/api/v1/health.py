from typing import Annotated

import redis as redis_lib
from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])

# These are exported for handlers in later plans to increment.
JOBS_TOTAL = Counter("redactly_jobs_total", "Jobs created", ["status", "tenant"])
JOB_DURATION = Histogram("redactly_job_duration_seconds", "Job duration by stage", ["stage"])
DETECTIONS_TOTAL = Counter(
    "redactly_detections_total", "Detections produced", ["layer", "type"]
)
LLM_CALLS_TOTAL = Counter(
    "redactly_llm_verifier_calls_total", "LLM verifier invocations", ["result"]
)
VERIFY_FAILURES_TOTAL = Counter(
    "redactly_redaction_verify_failures_total",
    "Post-redaction verify failures (Sev-1 — alert)",
)


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict:
    s = get_settings()

    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    redis_ok = False
    try:
        client = redis_lib.from_url(s.redis_url, socket_connect_timeout=1)
        client.ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False

    return {
        "status": "healthy" if db_ok and redis_ok else "degraded",
        "components": {"database": db_ok, "redis": redis_ok},
    }


@router.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
