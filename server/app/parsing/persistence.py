"""DocumentModel persistence backed by BlobStore.

Layout (keys are relative to the BlobStore root):

    parsed/<job_id>/document.json
    renders/<job_id>/page-<page>.png

The Job row's `parsed_document_uri` is set to the JSON URI; per-page renders
are looked up by deterministic key (no separate index needed).
"""

from __future__ import annotations

from io import BytesIO
from uuid import UUID

from app.core.storage import BlobStore
from app.parsing.document_model import DocumentModel, from_json, to_json


def _document_key(job_id: UUID) -> str:
    return f"parsed/{job_id}/document.json"


def _render_key(job_id: UUID, page: int) -> str:
    return f"renders/{job_id}/page-{page}.png"


def save_document_model(
    store: BlobStore,
    job_id: UUID,
    model: DocumentModel,
    page_renders: dict[int, bytes],
) -> tuple[str, dict[int, str]]:
    """Persist model JSON + page rasters. Returns (json_uri, renders_uri_map).

    The model's own `page_render_uris` is updated in place to the persisted URIs
    before serialization, so the JSON on disk is self-describing.
    """
    render_uris: dict[int, str] = {}
    for page, png_bytes in page_renders.items():
        uri = store.put(_render_key(job_id, page), BytesIO(png_bytes))
        render_uris[page] = uri
    model.page_render_uris = render_uris

    json_payload = to_json(model)
    json_uri = store.put(_document_key(job_id), BytesIO(json_payload.encode("utf-8")))
    return json_uri, render_uris


def load_document_model(store: BlobStore, job_id: UUID) -> DocumentModel:
    with store.get(_document_key(job_id)) as fh:
        payload = fh.read().decode("utf-8")
    return from_json(payload)
