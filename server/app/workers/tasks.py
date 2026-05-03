import structlog

from io import BytesIO
from uuid import UUID

from app.core.storage import get_blob_store
from app.db.models import Job, JobStatus
from app.db.session import SessionLocal
from app.parsing.parser import parse
from app.parsing.persistence import save_document_model
from app.workers.celery_app import celery_app
from app.workers.job_state import IllegalTransition, transition_job

log = structlog.get_logger(__name__)


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    """Smoke task — used by ops to confirm the worker is alive."""
    log.info("ping")
    return "pong"


@celery_app.task(name="app.workers.tasks.parse_job", bind=True)
def parse_job(self, job_id_str: str) -> dict:
    """Parse the source blob of a Job into a DocumentModel and persist it.

    State transitions: PENDING → PARSING → AWAITING_REVIEW
    (Detection runs between PARSING and AWAITING_REVIEW in Plan 3; for now
    we land directly in AWAITING_REVIEW so the smoke gate passes. Plan 3 will
    flip this to transition into DETECTING and let the detect_job task move
    to AWAITING_REVIEW.)
    """
    job_id = UUID(job_id_str)
    log.info("parse_job.start", job_id=str(job_id), task_id=self.request.id)
    store = get_blob_store()

    with SessionLocal() as db:
        try:
            job = transition_job(db, job_id, JobStatus.PARSING)
            db.commit()
        except IllegalTransition as e:
            log.error("parse_job.illegal_transition", job_id=str(job_id), error=str(e))
            return {"status": "skipped", "reason": str(e)}

        # Read the source blob.
        # Foundation's LocalBlobStore stores keys derived from blob URIs; we
        # need the key, not the file URI. The Job row stores the URI; for the
        # local backend the key is everything after `file://`+root or after
        # `blob+local://`. We rely on the Job row's `source_blob_uri` being a
        # `file://` URI for the local backend.
        source_uri: str = job.source_blob_uri
        source_key = _uri_to_key(source_uri, store)

        try:
            with store.get(source_key) as fh:
                blob_bytes = fh.read()

            model, renders = parse(blob_bytes, source_format=job.source_format)  # type: ignore[arg-type]
            json_uri, _ = save_document_model(store, job_id, model, renders)

            with SessionLocal() as db2:
                job2 = db2.get(Job, job_id)
                job2.parsed_document_uri = json_uri
                job2.page_count = model.page_count
                db2.commit()

            with SessionLocal() as db3:
                transition_job(db3, job_id, JobStatus.AWAITING_REVIEW)
                db3.commit()

            log.info(
                "parse_job.done",
                job_id=str(job_id),
                page_count=model.page_count,
                text_span_count=len(model.text_spans),
            )
            return {
                "status": "ok",
                "page_count": model.page_count,
                "text_span_count": len(model.text_spans),
                "parsed_document_uri": json_uri,
            }
        except Exception as e:  # noqa: BLE001 — explicitly catch-all to record failure
            log.exception("parse_job.failed", job_id=str(job_id))
            with SessionLocal() as db_err:
                try:
                    transition_job(
                        db_err, job_id, JobStatus.FAILED,
                        error={"stage": "parsing", "message": str(e)},
                    )
                    db_err.commit()
                except IllegalTransition:
                    pass
            raise


def _uri_to_key(uri: str, store) -> str:
    """Reverse the key→URI mapping for the LocalBlobStore.

    `LocalBlobStore.put` returns a `file://` URI rooted at storage_local_root.
    We compute the key by stripping the root prefix.
    """
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    if parsed.scheme == "file":
        # On Windows, urlparse leaves the leading slash in `path` (e.g. "/C:/...");
        # PyMuPDF doesn't care, but we do for the substring check.
        path = unquote(parsed.path).lstrip("/")
        root = str(store.root).replace("\\", "/").lstrip("/")
        if path.lower().startswith(root.lower()):
            return path[len(root):].lstrip("/")
    if parsed.scheme == "blob+local":
        return parsed.netloc + parsed.path
    raise ValueError(f"cannot derive key from uri: {uri!r}")
