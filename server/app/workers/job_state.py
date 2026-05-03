"""Job state-machine helper.

Centralizes valid transitions per spec §5.1. Workers (parse, detect, redact)
all call `transition_job` rather than mutating `job.status` directly so the
allowed-transition rules live in one place.
"""

from __future__ import annotations

from typing import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus

# Valid transitions per spec §5.1. Terminal states (COMPLETE, EXPIRED, FAILED,
# CANCELLED) are not keys — once there, no further transition is allowed.
_VALID_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.PARSING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.PARSING: frozenset({JobStatus.DETECTING, JobStatus.AWAITING_REVIEW, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.DETECTING: frozenset({JobStatus.AWAITING_REVIEW, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.AWAITING_REVIEW: frozenset({JobStatus.REDACTING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED}),
    JobStatus.REDACTING: frozenset({JobStatus.VERIFYING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.VERIFYING: frozenset({JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.COMPLETE: frozenset({JobStatus.EXPIRED}),
}


class IllegalTransition(Exception):
    """Raised when a worker attempts a transition not allowed by §5.1."""


def transition_job(
    db: Session,
    job_id: UUID,
    to_status: JobStatus,
    *,
    error: dict | None = None,
) -> Job:
    """Move a Job to `to_status` if the transition is permitted.

    On `FAILED`, the optional `error` dict is recorded on the row.
    Caller is responsible for the surrounding transaction commit.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise IllegalTransition(f"job {job_id} not found")

    allowed = _VALID_TRANSITIONS.get(job.status, frozenset())
    if to_status not in allowed:
        raise IllegalTransition(
            f"job {job_id}: cannot transition from {job.status.value} to {to_status.value}"
        )

    job.status = to_status
    if to_status == JobStatus.FAILED and error is not None:
        job.error = error
    db.flush()
    return job
