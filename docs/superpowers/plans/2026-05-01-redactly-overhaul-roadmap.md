# RedactLy V2 Overhaul — Roadmap

> **Reference spec:** `docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md`
>
> This roadmap names the six implementation plans that together deliver V1 of the spec, in dependency order. Each plan produces working software with a clear acceptance gate. Subsequent plan documents are written incrementally — when a plan nears completion, the next plan is drafted against the actual landed state, not the speculative spec text. This is intentional: it lets reality update the plan rather than locking in details before their dependencies exist.

## Plan Sequence

```
                ┌─────────────────────────────┐
                │ Plan 1 · Foundation         │  ← write detailed plan now
                │ FastAPI · Postgres · Celery │
                │ blob storage · local auth   │
                │ Docker Compose · CLI        │
                └────────────┬────────────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                     ▼
┌────────────────┐  ┌────────────────────┐  ┌──────────────────────┐
│ Plan 2 · Parsing│  │ Plan 3 · Detection│  │ Plan 5 · APIs + HITL │
│ DocumentModel  │  │ Layers A/B/C       │  │ State machine · OIDC │
│ PDF/image/OCR  │  │ Calibrator · Ollama│  │ WebSocket · audit    │
└────────┬───────┘  └─────────┬──────────┘  └──────────┬───────────┘
         │                    │                        │
         └─────────┬──────────┘                        │
                   ▼                                   ▼
        ┌────────────────────┐              ┌─────────────────────┐
        │ Plan 4 · Redaction │              │ Plan 6 · Frontend   │
        │ pdf_text · pdf_img │              │ PDF.js · overlay    │
        │ raster · verify    │              │ bulk ops · login    │
        └────────────────────┘              └─────────────────────┘
```

## Plans

### Plan 1 — Foundation

**Status:** detailed plan written (`2026-05-01-redactly-foundation.md`).

**Scope:** FastAPI app skeleton, Postgres + Alembic, Celery + Redis wiring, blob-storage abstraction with local FS implementation, structured logging, basic auth (local password + API keys; OIDC deferred to Plan 5), bootstrap CLI for first admin, Docker Compose stack, Dockerfile, Nginx TLS terminator, backup/restore scripts.

**Acceptance gate:**
- `docker compose up -d` brings every service to healthy.
- `python -m cli admin create --email ... --password ...` (run inside the `api` container) provisions the first admin row.
- `POST /api/v1/auth/login` returns a valid JWT for that admin.
- `GET /api/v1/auth/me` returns the admin's identity given the JWT.
- `GET /api/v1/health` returns reachability of DB + Redis + (Ollama if configured).
- `GET /api/v1/metrics` returns Prometheus-formatted output with the basic counters declared.
- Alembic head migration creates the full schema from spec §5.2 (all six tables) — even tables not yet used; this avoids historical-data migrations later.

### Plan 2 — Document parsing

**Depends on:** Plan 1 (uses the worker process and config infra).

**Scope:** `parsing/document_model.py` dataclasses (TextSpan, PageImage, DocumentModel), `parsing/pdf_parser.py` (text-layer + per-image OCR with bbox preservation), `parsing/image_parser.py` (multi-page TIFF, HEIC, WebP, JPG, PNG), `parsing/ocr_preprocessing.py` (deskew via Hough, CLAHE, fastNlMeansDenoising). A new Celery task `parse_job` that turns an uploaded blob into a persisted DocumentModel artifact in object storage.

**Acceptance gate:**
- Parsing a known PDF and a known image produces a `DocumentModel` with non-empty `text_spans` (mix of `text_layer` and `ocr` sources) and per-page-image OCR spans.
- 200 DPI page renders are written to blob storage and addressable by `/api/v1/jobs/{id}/preview/{page}`.
- A multi-page TIFF parses into one DocumentModel with `page_count > 1`.

### Plan 3 — Detection engine

**Depends on:** Plan 1 (workers, config, persistence) and Plan 2 (DocumentModel as input).

**Scope:** `detection/schema.py` Detection dataclass; `detection/rules/presidio_engine.py` and the per-region recognizer modules (generic, financial, india, us, uk, eu, medical) with all checksum validators (Verhoeff, Luhn, mod-11 NHS, IBAN mod-97); `detection/rules/context_scorer.py` driven by YAML; `detection/ner/transformer.py` with GLiNER default; `detection/llm/verifier.py` + `prompts.py` + `schema.py` covering trigger policy, Ollama JSON-mode batch calls, fallbacks; `detection/calibrator.py` with score normalization, overlap merging, deny-list filtering, confidence buckets. A new Celery task `detect_job` that consumes a parsed DocumentModel and writes Detection rows. Ollama service in compose plus a model-warmup task at first start.

**Acceptance gate:**
- Detection runs end-to-end on a known PDF and produces Detection rows with `evidence_jsonb` populated per the normative shape in spec §3.5.
- LLM verifier fires on the documented trigger conditions only; Ollama unreachable → verifier skipped, Detection row records `evidence_jsonb.llm.skipped = "unreachable"`.
- Aadhaar Verhoeff checksum rejects 12-digit non-Aadhaar strings that the current regex would accept.
- Calibrator buckets are reproducible across runs on the same input.

### Plan 4 — Redaction pipeline

**Depends on:** Plan 2 (DocumentModel) and Plan 3 (Detection rows).

**Scope:** `redaction/applier.py` dispatcher; `redaction/pdf_text.py`, `redaction/pdf_image.py`, `redaction/image_raster.py`; `redaction/metadata.py` for PDF/image metadata + EXIF scrub with post-write integrity check; `redaction/verifier.py` for the mandatory verification pass. The strict per-page redaction ordering from spec §4.6 is implemented in one place. A new Celery task `redact_job` that consumes approved Detection rows and writes the redacted blob.

**Acceptance gate:**
- Three sample PDFs (text-only, embedded-image-only, mixed) each redact end-to-end and the verifier finds no surviving approved spans.
- A raw-image input round-trips to a redacted image of the same format.
- The post-write metadata check catches an intentionally-not-cleared XMP entry and fails the job with `VERIFY_FAILED`.

### Plan 5 — Job lifecycle, APIs, OIDC, audit

**Depends on:** Plans 1, 2, 3, 4.

**Scope:** Job state machine with explicit transitions; `api/v1/jobs.py` (upload + list + get + cancel + preview), `api/v1/detections.py` (list + bulk PATCH + auto-approve), `api/v1/redactions.py` (commit + download); WebSocket broadcaster for job updates; OIDC client integration in `core/auth.py` with JIT user provisioning; full audit-event writer wired into every state transition and approval action; admin endpoints for users, audit log, API keys; CSV export for audit log; rate limiting via `slowapi`.

**Acceptance gate:**
- A full happy-path run via API: upload → poll/WebSocket through `AWAITING_REVIEW` → bulk-approve detections → commit → download redacted artifact.
- An OIDC login against a Keycloak test container provisions a user and returns a working JWT.
- Audit log records every state transition + every approval/rejection/edit; CSV export contains the same rows.
- Postgres role grants prevent UPDATE/DELETE on `audit_events`.

### Plan 6 — Frontend HITL

**Depends on:** Plan 5 (the API surface it consumes).

**Scope:** Replace the current single-page `client/src/` tree with a routed app: `/upload`, `/jobs`, `/jobs/:id/review`, `/admin/users`, `/admin/audit`, `/auth/login`. PDF.js viewer + canvas image viewer + SVG overlay layer. Virtualized detection list with filters and bulk operations. Two-way sync between viewer and list. Edit-replacement-text per detection. Keyboard shortcuts (j/k/a/r/e). WebSocket subscription for live job state. Login UI for both local and OIDC paths.

**Acceptance gate:**
- A reviewer can upload, watch the state pill transition to `AWAITING_REVIEW`, see overlays + the detection list, bulk-approve all HIGH, commit, and download.
- All shadcn primitives + Tailwind config from the existing `client/` tree carry over (no UI regression for primitives).
- Multi-page PDF and multi-page TIFF both render and overlay correctly.

## Cross-cutting notes

- **Tests are deferred** per project owner. Each plan's acceptance gate is verified by manual smoke checks documented in the plan, not automated tests. The verification pass in the redaction pipeline (Plan 4) and the post-write metadata check provide *some* runtime safety net but are not substitutes for tests. Plan to backfill tests in V2 before further detection-engine changes (already noted in spec §8).
- **Frequent commits** within every plan. Each task ends in a commit. CI is V2; for V1 every commit is local on `main` (or a feature branch per plan if the project owner prefers — recommended for the larger plans).
- **Worktree isolation** is recommended per the superpowers `using-git-worktrees` skill for any plan touching the same paths the previous plan modified, but this is a single-developer hackathon repo so it's optional.
- **Plan 2 and Plan 3 can be developed in parallel** (different files, both depend only on Plan 1). Plan 4 needs both done.
- **Each plan's first task is to read the spec and the relevant prior-plan completion state** so the subagent or human starts with current ground truth, not stale assumptions.

## What's not in this roadmap (per spec)

- Office formats (DOCX/XLSX/PPTX) — V2.
- Email/CSV/JSON parsing — V3.
- Storage connectors (S3 sweeps, SharePoint, Gmail) — V3.
- Multi-tenant runtime — V2.
- Bundled observability stack overlay — V2.
- Helm charts / K8s manifests — V2.
- Tests + CI/CD — V2 (planned backfill before further detection work).
