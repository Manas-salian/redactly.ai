# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0-parsing] - 2026-05-03

### Added
- `DocumentModel`, `TextSpan`, `PageImage`, `Bbox` dataclasses with JSON serialization (`server/app/parsing/document_model.py`)
- OCR preprocessing pipeline: deskew (Hough), CLAHE, fast non-local-means denoising (`server/app/parsing/ocr_preprocessing.py`)
- Tesseract OCR wrapper with per-word bboxes and confidence filtering (`server/app/parsing/ocr.py`)
- PDF parser: text-layer extraction + per-embedded-image OCR + 200 DPI page rasters (`server/app/parsing/pdf_parser.py`)
- Image parser: JPG, PNG, TIFF (single + multi-page), HEIC, WebP (`server/app/parsing/image_parser.py`)
- Top-level `parse(...)` dispatcher (`server/app/parsing/parser.py`)
- Blob-storage persistence for `DocumentModel` JSON + page-render PNGs (`server/app/parsing/persistence.py`)
- `parse_job(job_id)` Celery task with state-machine transitions (`server/app/workers/tasks.py`)
- `transition_job(...)` job state-machine helper (`server/app/workers/job_state.py`)
- CLI: `python -m cli parsing parse-file <path>` and `python -m cli parsing enqueue-parse <job_id>`
- Smoke fixture builder (`server/fixtures/build.py`)

### Changed
- `Dockerfile` adds `tesseract-ocr`, `tesseract-ocr-eng`, `libheif1`, `libgl1`, `libglib2.0-0` system packages
- `pyproject.toml` adds `pymupdf`, `pytesseract`, `pillow`, `pillow-heif`, `opencv-python-headless`, `numpy`
- `jobs` table gains a `parsed_document_uri TEXT` column (migration `0002_jobs_parsed_uri.py`)

## [0.2.0-foundation] - 2026-05-01

This release establishes the complete V2 application skeleton. No document
processing, detection, or redaction is implemented yet — those land in Plans 2–4.

### Added

- **FastAPI + Uvicorn application skeleton** — `server/app/main.py` with lifespan
  hooks, CORS middleware, and request-ID middleware (`server/app/logging.py`).
  Production boot guard: refuses to start with default `JWT_SECRET`.
- **PostgreSQL schema + Alembic migrations** — full six-table schema
  (`tenants`, `users`, `api_keys`, `jobs`, `detections`, `audit_events`) in a
  single initial migration (`server/app/db/migrations/versions/0001_initial.py`).
  All `tenant_id` columns present from day one; no historical migrations needed
  when multi-tenant runtime lands in V2.
- **Celery + Redis worker scaffolding** — `server/app/workers/celery_app.py`
  wired to the Redis broker and result backend; `server/app/workers/tasks.py`
  with a `ping` smoke task confirming worker liveness.
- **Argon2 + HS256 JWT auth** — password hashing and verification
  (`server/app/core/auth.py`); access token (15 min) and refresh token (7 day)
  issuance; `decode_token` with full claims validation.
- **API keys with O(1) prefix lookup** — `rk_<8-char-prefix><secret>` format;
  prefix stored unhashed for fast row selection; full key Argon2-hashed;
  plaintext returned exactly once at creation. See `server/app/core/security.py`
  and `server/app/api/v1/admin.py`.
- **Role-based access control** — `admin`, `reviewer`, `viewer` roles;
  `require_role(*allowed)` FastAPI dependency; `current_user` dependency handling
  both JWT bearer and `X-API-Key` auth paths.
- **slowapi rate limiting** — `POST /api/v1/auth/login` limited to 10
  requests/minute/IP via a module-level `Limiter` in `server/app/core/rate_limit.py`.
- **Structured logging + request-ID middleware** — `structlog` JSON to stdout;
  `RequestIdMiddleware` assigns and propagates `x-request-id` on every request.
- **Audit event writer** — `write_audit_event(...)` helper in
  `server/app/core/audit.py`; wired into login and API-key create/revoke.
- **BlobStore abstraction** — `BlobStore` abstract interface with `put`, `get`,
  `delete`, `signed_url`; `LocalBlobStore` backed by filesystem in
  `server/app/core/storage.py`. S3/MinIO interface stubbed for later plans.
- **`/auth/login` and `/auth/me` endpoints** — `server/app/api/v1/auth.py`.
- **`/admin/api-keys` create/list/revoke endpoints** — `server/app/api/v1/admin.py`.
- **`/health` and `/metrics` endpoints** — DB + Redis liveness check; Prometheus
  counter and histogram declarations for all pipeline metrics (counters land at
  zero until Plans 3–4). See `server/app/api/v1/health.py`.
- **Typed runtime config** — `pydantic-settings` `Settings` class in
  `server/app/config.py`; covers all env vars with typed defaults; `@lru_cache`
  singleton.
- **Typer bootstrap CLI** — `server/cli.py` with `python -m cli migrate`
  (Alembic head) and `python -m cli admin create` (idempotent admin bootstrap).
- **Dockerfile** — Python 3.12-slim with `uv`-installed deps; non-root `appuser`
  (UID 5000); production-ready image at `redactly-server:foundation`.
- **Docker Compose stack** — `docker-compose.yml` orchestrates: `api`, `worker`,
  `beat`, `postgres`, `redis`, `ollama`, `nginx`. Services depend on health
  checks. Compose project name pinned to `redactly`.
- **GPU overlay placeholder** — `compose.gpu.yml` adds NVIDIA device reservations
  for `ollama` and `worker`; will be populated in Plan 3 with model switch.
- **Nginx TLS terminator** — `nginx/nginx.conf` with TLS 1.2/1.3, HSTS,
  X-Frame-Options, X-Content-Type-Options, Referrer-Policy; dev cert generator
  via openssl documented in README.
- **Backup/restore scripts** — `ops/backup.sh` (pg_dump + blob volume snapshot
  into a timestamped tarball) and `ops/restore.sh`.
- **pyproject.toml + uv.lock** — `server/pyproject.toml` with pinned dependency
  ranges; `server/uv.lock` committed for reproducible installs.
- **Documentation suite** — `README.md` (project landing page), `docs/architecture.md`,
  `docs/api.md`, `docs/operations.md`, `docs/contributing.md`, `docs/security.md`,
  `ROADMAP.md`, `CHANGELOG.md`.

### Changed

- **Replaced Flask + Presidio app** with FastAPI scaffold. The new server lives
  at `server/`; the old server is retained read-only at `server.legacy/`.
- **Replaced V1 README** with the V2 boot guide (commit `ccf4d4e`), then
  expanded to the full project landing page in this release.

### Removed

- **V1's synchronous in-request redaction path** — the Flask route that called
  `hybrid_detector` and `ocr_redaction` synchronously in the HTTP request is
  gone. Detection and redaction will run as async Celery tasks in Plans 2–4.
- **Ad-hoc `start.sh`** — replaced by Docker Compose.

## [0.1.0] - 2025-05-01

Initial Flask + Presidio implementation (legacy; retained at `server.legacy/`).

[Unreleased]: https://github.com/your-org/redactly.ai/compare/v0.2.0-foundation...HEAD
[0.2.0-foundation]: https://github.com/your-org/redactly.ai/compare/v0.1.0...v0.2.0-foundation
[0.1.0]: https://github.com/your-org/redactly.ai/releases/tag/v0.1.0
