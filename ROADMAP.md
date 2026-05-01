# Roadmap

This file summarises the six implementation plans that deliver RedactLy V1,
plus the V2 and V3 follow-on work. For full plan task lists, see
`docs/superpowers/plans/`. For the design spec, see
`docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md`.

Plans 2 and 3 can run in parallel (different modules, both depend only on Plan 1).
Plan 4 requires both Plan 2 and Plan 3. Plans 5 and 6 require Plan 4.

## Plan 1 — Foundation

**Status:** Current — shipped 2026-05-01.

**What shipped:** FastAPI + Uvicorn skeleton; Postgres 16 + Alembic full schema
(all six tables); Celery + Redis worker scaffolding; Argon2 + HS256 JWT; API
keys with O(1) prefix lookup; role-based access control; slowapi rate limiting;
structured logging + request-ID middleware; BlobStore abstraction (local FS
implementation); `/auth`, `/admin`, `/health`, `/metrics` endpoints; Typer
bootstrap CLI; Dockerfile; Docker Compose stack with GPU overlay placeholder;
Nginx TLS terminator + dev cert; backup/restore scripts.

**Acceptance gate:** `docker compose up -d` brings every service healthy; `admin
create` provisions an admin; `POST /auth/login` returns a valid JWT; `GET
/health` reports DB + Redis up; `GET /metrics` returns Prometheus output.

**Outcome:** Working application skeleton that every subsequent plan builds on.
Unlocks Plans 2, 3, and 5 in parallel.

See [CHANGELOG.md](CHANGELOG.md) for the full commit list.

---

## Plan 2 — Document parsing

**Status:** Next — not yet started.

**Scope:** `DocumentModel` dataclasses (`TextSpan`, `PageImage`, `DocumentModel`
in `server/app/parsing/document_model.py`); PDF parser (`parsing/pdf_parser.py`)
using PyMuPDF with per-image OCR and bbox preservation; image parser
(`parsing/image_parser.py`) supporting JPG, PNG, TIFF (single + multi-page),
HEIC, WebP; OCR preprocessing (`parsing/ocr_preprocessing.py`) for deskew via
Hough transform, CLAHE contrast enhancement, and `fastNlMeansDenoising`. A
`parse_job` Celery task that converts an uploaded blob into a persisted
`DocumentModel` artifact.

**Acceptance gate:** A known PDF and a known image each produce a `DocumentModel`
with non-empty `text_spans`; a multi-page TIFF produces `page_count > 1`; 200
DPI page renders are written to blob storage.

**Outcome:** Any document type the product supports can be parsed into a uniform
structure. Unlocks Plan 4 (with Plan 3).

---

## Plan 3 — Detection engine

**Status:** Next — not yet started (can run parallel to Plan 2).

**Scope:** Three-layer hybrid detection pipeline. Layer A: Presidio-based rules
with region modules (generic, financial, india, us, uk, eu, medical) and
mandatory checksum validators (Verhoeff for Aadhaar, Luhn for cards, mod-11 for
NHS, mod-97 for IBAN); YAML-driven context scorer. Layer B: GLiNER transformer
NER (`urchade/gliner_multi_pii-v1`; multilingual; runtime label list). Layer C:
Ollama LLM verifier (phi3.5-mini on CPU, qwen2.5-7b on GPU) with selective
trigger policy, JSON-mode batching, and graceful fallback. Calibrator with score
normalization, overlap merging, deny-list filtering, and HIGH/MED/LOW confidence
buckets. A `detect_job` Celery task.

**Acceptance gate:** Detection runs end-to-end on a known PDF and writes
`Detection` rows with `evidence_jsonb` populated; LLM verifier fires on trigger
conditions and gracefully skips if Ollama is unreachable; Aadhaar Verhoeff
checksum rejects non-Aadhaar 12-digit strings.

**Outcome:** PII is detected with high recall and precision. Confidence buckets
enable auto-approve workflows. Unlocks Plan 4 (with Plan 2).

---

## Plan 4 — Redaction pipeline

**Status:** Upcoming — requires Plans 2 and 3.

**Scope:** Redaction applier dispatcher (`redaction/applier.py`); PDF text-layer
applier (`redaction/pdf_text.py`); PDF embedded-image applier
(`redaction/pdf_image.py`) with strict per-page ordering to avoid cross-ref
corruption; raster image applier (`redaction/image_raster.py`); metadata scrub
(`redaction/metadata.py`) for PDF info dict, XMP, EXIF; mandatory verification
pass (`redaction/verifier.py`) that re-detects on the output and fails the job
if any approved span survives. A `redact_job` Celery task.

**Acceptance gate:** Three sample PDFs (text-only, embedded-image-only, mixed)
each redact end-to-end with zero surviving approved spans; raw image input
round-trips; post-write metadata check catches an intentionally-not-cleared XMP
entry.

**Outcome:** Documents are redacted reliably and verifiably. Unlocks Plan 5
(full API surface) and Plan 6 (frontend).

---

## Plan 5 — Job lifecycle, APIs, OIDC, audit

**Status:** Upcoming — requires Plan 4.

**Scope:** Job state machine with explicit transitions; `api/v1/jobs.py` (upload,
list, get, cancel, preview); `api/v1/detections.py` (list, bulk PATCH,
auto-approve); `api/v1/redactions.py` (commit, download); WebSocket broadcaster
for live job state; OIDC client with JIT user provisioning and role mapping;
full audit-event wiring into every state transition and detection decision; admin
endpoints for users, audit log (with CSV export), API keys; Celery beat task for
blob + detection-text purge at expiry; rate limiting on job upload endpoint.

**Acceptance gate:** Full happy-path run via API: upload → poll through
`AWAITING_REVIEW` → bulk-approve → commit → download. OIDC login against a
test IdP provisions a user and returns a working JWT. Audit log CSV export
contains every state transition.

**Outcome:** The system is fully operable via API. Unlocks Plan 6.

---

## Plan 6 — Frontend HITL

**Status:** Upcoming — requires Plan 5.

**Scope:** Replace the current single-page client with a routed app: `/upload`,
`/jobs`, `/jobs/:id/review`, `/admin/users`, `/admin/audit`, `/auth/login`.
PDF.js viewer + canvas image viewer + SVG overlay layer rendering detections as
colored rectangles (red=pending, green=approved, grey=rejected, amber=edited).
Virtualized detection list with type/page/bucket/layer filters and bulk
operations. Two-way sync between viewer overlay and sidebar. Per-detection
edit-replacement-text. Keyboard shortcuts (`j/k` navigate, `a` approve, `r`
reject, `e` edit). WebSocket subscription for live job state.

**Acceptance gate:** A reviewer can upload, watch the state pill reach
`AWAITING_REVIEW`, see overlays on the document, bulk-approve all HIGH
detections, commit, and download the redacted file. Multi-page PDF and
multi-page TIFF both render and overlay correctly.

**Outcome:** The product is end-to-end usable by a human reviewer without API
calls.

---

## V2

**Status:** Future — scope defined, not scheduled.

Items deferred from V1:

| Item | Notes |
|---|---|
| Office formats (DOCX/XLSX/PPTX) | Requires `python-docx`, `openpyxl`, `python-pptx` parsers and matching redaction appliers |
| Multi-tenant runtime | Row isolation at repository layer; per-tenant configuration UI; OIDC role-map UI |
| Helm chart / Kubernetes manifests | For deployments beyond single-host Docker Compose |
| Observability stack overlay | Grafana + Loki + Tempo compose overlay |
| Tests + CI | pytest suite backfill across all six plans; GitHub Actions pipeline |
| RS256 JWT | Asymmetric JWT for federated service verification |
| Config-driven CORS origins | Move hardcoded `allow_origins` to an env var |
| Postgres audit row-level grants | `REVOKE UPDATE, DELETE ON audit_events FROM app_role` |

**Acceptance gate for V2:** Office format round-trips pass the same verification
gate as PDF; CI is green on main; multi-tenant smoke test passes with two isolated
tenant datasets.

**Outcome:** Enterprise-deployable with full format coverage, security hardening,
and automated test coverage.

---

## V3

**Status:** Future — not yet planned in detail.

- Email formats (EML / MSG)
- Plain-text and CSV processing
- Storage connectors (S3 sweep jobs, SharePoint, Gmail)
- Automated discovery jobs (crawl + batch-redact)
- Public client SDKs (Python, TypeScript)
- UI internationalization
