# Architecture

This document describes what RedactLy V2 is built from and how the pieces fit
together. It is written for a new contributor or evaluator who wants to
understand the system in roughly ten minutes. For full design rationale, see
`docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md` (the spec).

## Architecture at a glance

```
                  ┌──────────────────────────────────────────────┐
                  │  Nginx (TLS terminator)                      │
                  │  ports 443 / 8080                            │
                  └─────────────────┬────────────────────────────┘
                                    │
                  ┌─────────────────▼────────────────────────────┐
                  │  FastAPI  (REST + WebSocket*)                 │
                  │  /api/v1/auth  /api/v1/admin  /health        │
                  │  /metrics                                     │
                  └────┬─────────────┬────────────────┬──────────┘
                       │             │                │
              ┌────────▼──────┐  ┌───▼────┐  ┌───────▼───────┐
              │  PostgreSQL   │  │ Redis  │  │ Blob storage  │
              │  (state + audit│  │(queue +│  │(local FS /    │
              │   all tables)  │  │result) │  │ S3 / MinIO*)  │
              └───────────────┘  └────────┘  └───────────────┘
                                    │
              ┌─────────────────────▼──────────────────────────┐
              │  Celery workers                                 │
              │    parse_job*   detect_job*   redact_job*       │
              │    expire_blobs*                                 │
              └──────────────────────┬──────────────────────────┘
                                     │
                              ┌──────▼──────┐
                              │   Ollama*   │
                              │  (LLM       │
                              │  verifier)  │
                              └─────────────┘
```

Items marked `*` are aspirational — they appear in this diagram because the
infrastructure for them (database columns, table structures, Celery wiring,
Ollama container) is already in place, but the worker tasks and API routes that
drive them land in Plans 2–5. See [What's actually built today](#whats-actually-built-today).

The frontend (React + PDF.js + HITL overlay) is rewritten in Plan 6 and is not
shown here.

## Why this stack

| Choice | Reason |
|---|---|
| **FastAPI** | Native async, auto-generated OpenAPI docs, strong Pydantic integration. Replaces Flask which was synchronous and had no job queue. |
| **PostgreSQL** | ACID guarantees for the audit trail; JSONB for flexible `evidence` and `config` columns; `ARRAY` type for API key scopes. |
| **Celery + Redis** | Proven Python task queue. Workers run detection and redaction outside the request/response cycle so uploads return immediately with a `job_id`. |
| **Ollama** | Runs quantized LLMs (phi3.5-mini, qwen2.5-7b) fully on-device. The product's core promise — no cloud APIs in any data path — depends on a local LLM runtime. |
| **SQLAlchemy 2.0 (sync)** | Shared across both the FastAPI request handlers and the Celery workers without an async/sync split in the session layer. |
| **uv** | Fast dependency resolution and lockfile; replaces pip-tools for the Python 3.12 project. |

## Data flow

The following is the target flow for a complete redaction job. Steps marked
`[NOT YET BUILT]` require Plans 2–5.

```
1. Upload          → POST /api/v1/jobs (multipart)           [NOT YET BUILT — Plan 5]
                     write blob to BlobStore
                     create Job row (status=PENDING)
                     enqueue parse_job task

2. Parse           → Celery: parse_job                       [NOT YET BUILT — Plan 2]
                     PDF/image → DocumentModel
                     status=PARSING → DETECTING

3. Detect          → Celery: detect_job                      [NOT YET BUILT — Plan 3]
                     DocumentModel through 3-layer pipeline
                     write Detection rows
                     status=DETECTING → AWAITING_REVIEW

4. HITL review     → human approves/rejects via UI           [NOT YET BUILT — Plan 6]
                     or bulk-approve via API                  [NOT YET BUILT — Plan 5]

5. Redact          → Celery: redact_job                      [NOT YET BUILT — Plan 4]
                     apply approved Detections to original
                     status=REDACTING → VERIFYING

6. Verify          → re-parse + re-detect against approved spans [NOT YET BUILT — Plan 4]
                     any survivor → FAILED (Sev-1)
                     status=VERIFYING → COMPLETE

7. Finalize        → scrub metadata from output blob
                     write redacted_blob_uri + sha256 on Job row
                     client downloads via signed URL          [NOT YET BUILT — Plan 5]
```

Expired jobs are purged by a Celery beat task: blobs deleted, `detections.text`
and `detections.bbox` nulled (the matched text is PII and must not outlive the
source blob — see spec §5.7). The beat process runs today; the purge task
implementation is part of Plan 5.

## Detection engine (target)

The detection pipeline is a three-layer hybrid that lands in Plan 3. The schema
columns and enum values for all three layers are already in the database.

**Layer A — Deterministic rules** (`detection/rules/`): Presidio-based recognizers
with region modules (generic, financial, india, us, uk, eu, medical). Every
entity type that has a checksum (Aadhaar Verhoeff, credit-card Luhn, NHS mod-11,
IBAN mod-97) is validated — not just pattern-matched. A YAML-driven context
scorer boosts or penalises scores based on surrounding tokens.

**Layer B — Transformer NER** (`detection/ner/`): GLiNER `urchade/gliner_multi_pii-v1`
by default (multilingual, runtime label list, ~250 MB). Outputs PERSON, ORG,
ADDRESS, DATE, and related types with character-level offsets mapped back to
bounding boxes from the parser.

**Layer C — LLM verifier** (`detection/llm/`): Ollama JSON-mode calls to
phi3.5-mini (CPU) or qwen2.5-7b (GPU). Fires selectively — roughly 10–25% of
spans — when layers A and B disagree, when the pre-calibrator score falls in
[0.5, 0.8], or when the span is structurally flagged as a table cell. Failure
modes (Ollama unreachable, JSON parse error) are graceful: the span is passed
through on A+B scores and the skip reason is recorded in `evidence_jsonb`.

A **Calibrator** follows the three layers: per-source score normalization, overlap
merging, deny-list filtering, and final bucketing into HIGH (≥0.85), MED
(0.60–0.85), LOW (<0.60). See spec §3 for the full design.

## Privacy posture

- **Local processing only.** No detection or redaction step calls an external
  API. Ollama hosts the LLM model on the same deployment.
- **Blob expiry purge.** At `expires_at` (default 24 h, per-tenant configurable),
  the source blob and redacted blob are deleted from storage and their URIs
  nulled on the Job row.
- **Detection text purge.** `detections.text`, `detections.approved_text`, and
  `detections.bbox` are nulled at expiry. The matched substring is by definition
  the PII being redacted; it must not outlive the source document.
- **Append-only audit.** `audit_events` rows are never updated or deleted by
  application code. A V2 hardening item adds Postgres role grants that enforce
  this at the database level.
- **Audit payloads carry no PII.** The `write_audit_event` helper documents that
  callers are responsible for hashing or omitting sensitive fields from the
  `payload` argument (see `server/app/core/audit.py`).
- **HITL review.** No detection is redacted without a human approval step (or an
  explicit auto-approve policy set by an admin). This creates the defensible
  audit trail enterprise compliance teams require.

## Single-tenant V1 / multi-tenant V2

Every table carries a `tenant_id` column with a foreign key to `tenants`. The
application runtime today creates one tenant (`default`) at bootstrap time and
all users, keys, jobs, and audit rows reference it. Multi-tenant runtime path
(row isolation at the repository layer, per-tenant configuration, OIDC role
mapping) is a V2 item. The schema was designed this way deliberately — V2
multi-tenant work does not require historical-data migrations.

## What's actually built today

The following modules are present and functional in `server/app/`:

| Module | What it does |
|---|---|
| `main.py` | FastAPI app factory, lifespan hooks, middleware wiring, router mounting |
| `config.py` | `pydantic-settings` typed config; reads `.env`; single source of truth for all runtime settings |
| `logging.py` | `structlog` JSON logging config; `RequestIdMiddleware` that assigns and propagates `x-request-id` |
| `api/v1/auth.py` | `POST /auth/login`, `GET /auth/me` |
| `api/v1/admin.py` | `POST /admin/api-keys`, `GET /admin/api-keys`, `DELETE /admin/api-keys/{id}` |
| `api/v1/health.py` | `GET /health` (DB + Redis liveness), `GET /metrics` (Prometheus) |
| `api/v1/__init__.py` | Router aggregator |
| `core/auth.py` | Argon2 password hash/verify, JWT issue and decode, `TokenClaims` model |
| `core/security.py` | `current_user` FastAPI dependency (JWT + API-key paths), `require_role` decorator |
| `core/audit.py` | `write_audit_event(...)` helper; appends to `audit_events` |
| `core/rate_limit.py` | Module-level `slowapi` `Limiter` instance for handler-side decorators |
| `core/storage.py` | `BlobStore` abstract interface + `LocalBlobStore` implementation |
| `db/models.py` | All six ORM models: `Tenant`, `User`, `ApiKey`, `Job`, `Detection`, `AuditEvent` |
| `db/session.py` | SQLAlchemy engine, `SessionLocal`, `get_db` dependency |
| `db/migrations/` | Alembic env + initial migration (`0001_initial.py`) creating all six tables |
| `workers/celery_app.py` | Celery application wired to Redis broker and result backend |
| `workers/tasks.py` | `ping` smoke task (confirms worker liveness); real tasks land in Plans 2–4 |

The CLI (`server/cli.py`) provides `python -m cli migrate` and `python -m cli admin create`.

## Where to read more

- **Full design rationale:** `docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md`
- **Six-plan roadmap:** `docs/superpowers/plans/2026-05-01-redactly-overhaul-roadmap.md`
- **Foundation implementation plan:** `docs/superpowers/plans/2026-05-01-redactly-foundation.md`
- **API reference:** [docs/api.md](api.md)
- **Operations runbook:** [docs/operations.md](operations.md)
