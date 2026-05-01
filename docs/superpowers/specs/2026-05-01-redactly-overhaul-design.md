# RedactLy V2 Overhaul — Design Spec

- **Date:** 2026-05-01
- **Status:** Draft (pending review)
- **Author:** mansx@eg.dk + collaborator (Claude)
- **Scope:** Full rewrite of the RedactLy.AI document-redaction product into a privacy-first, fully-local, enterprise-grade tool.

## 1. Goals and non-goals

### 1.1 Goals
- **Industry-grade PII detection** with significantly higher recall and precision than the current Presidio + spaCy `en_core_web_md` setup. Detection must be context-aware (correctly handling label/value table cells, mixed languages, transliterated names) and calibrated (a confidence score that maps predictably to "should we burn this in without review").
- **Reliable image and PDF redaction.** The current image-redaction pass calls PyMuPDF methods that don't exist (`doc.delete_image`, `doc.add_image_ref`, `page.replace_image`) and corrupts PDFs in subtle ways; this is the most visible "sloppy" part. V2 must redact embedded PDF images and raw image inputs reliably and verifiably.
- **Privacy-first, fully local processing.** No data path may call cloud APIs (OpenAI, AWS Comprehend, GCP DLP). All inference — NER, LLM verifier — runs inside the deployment. This is the product's core differentiator.
- **Human-in-the-loop (HITL) review workflow.** Detect → Review → Approve → Redact, with per-detection control and bulk operations. This is what enterprise compliance teams require and what creates a defensible audit trail.
- **Self-hostable in one command** (`docker compose up -d`) with optional GPU acceleration via overlay (`compose.gpu.yml`). CPU floor; GPU upsell.
- **Auditable.** Every state change and approval decision is recorded in an append-only audit log with actor, action, target, IP, and user-agent.

### 1.2 Non-goals (V1)
- Office formats (DOCX/XLSX/PPTX) — V2.
- Email formats (EML/MSG) — V3.
- Storage connectors (S3 sweeps, SharePoint, Gmail) — V3.
- Multi-tenant runtime (the schema supports it; the runtime path does not) — V2.
- Bundled observability stack (Grafana/Loki/Tempo) — V2.
- Tests — explicitly deferred per project owner.
- Helm charts / K8s manifests — V2.
- ML training/fine-tuning pipeline — out of scope; V1 ships pretrained models only.
- UI internationalization — V3.

## 2. Architecture

### 2.1 Stack changes from current V1

| Concern | Current | V2 |
|---|---|---|
| API server | Flask + Gunicorn (sync) | FastAPI + Uvicorn (async-native, OpenAPI free) |
| Job execution | In-request, blocking | Celery workers + Redis broker |
| Persistence | None (1h temp dir) | PostgreSQL + pluggable blob storage (local FS / S3 / MinIO) |
| Detection | Presidio + spaCy `en_core_web_md` | Three-layer hybrid: regex/Presidio → transformer NER → local LLM verifier |
| LLM runtime | None | Ollama (`phi-3.5-mini-instruct` Q4 on CPU; `qwen2.5-7b-instruct` Q4 on GPU) |
| Frontend | React 18 + shadcn (single-page) | Same stack + PDF.js + overlay layer + multi-page review UI |
| Auth | None / CORS open | OIDC + local accounts (Argon2) + API keys (scoped) |
| Image redaction | Buggy in-place rewrite | Render-and-overlay; mandatory verification pass |
| Observability | `print()` | Structured JSON logs + `/metrics` Prometheus endpoint (basic) |
| Deployment | Ad-hoc `start.sh` | Single `docker-compose.yml` + `compose.gpu.yml` overlay |

### 2.2 Component diagram (logical)

```
┌──────────────┐      ┌─────────────────────────────────────────────┐
│  Frontend    │◀────▶│  FastAPI (REST + WebSocket for job updates) │
│  (Vite/React)│      └────────────┬────────────────────────────────┘
└──────────────┘                   │
                                   ▼
                ┌──────────────────────────────────┐
                │  Postgres   │ Redis   │ MinIO/FS │
                │  (state)    │ (queue) │ (blobs)  │
                └──────┬───────────┬────────┬──────┘
                       │           │        │
                       ▼           ▼        ▼
                ┌────────────────────────────────────┐
                │  Celery worker(s) — detect & redact│
                │   DetectionPipeline                │
                │     1. Parser (PDF/image → blocks) │
                │     2. Layer A: Rules + Presidio   │
                │     3. Layer B: Transformer NER    │
                │     4. Layer C: LLM verifier ⇄ Ollama
                │     5. Calibrator + dedup          │
                │   RedactionApplier                 │
                │     - PDF text-layer redaction     │
                │     - PDF image-layer redaction    │
                │     - Image (raster) redaction     │
                │     - Metadata scrub               │
                └─────────────┬──────────────────────┘
                              ▼
                       ┌────────────┐
                       │  Ollama    │
                       └────────────┘
```

### 2.3 Module layout (server)

```
server/
  app/
    main.py                      # FastAPI app; router registration; lifespan hooks
    config.py                    # pydantic-settings; env-driven runtime config
    api/
      v1/
        jobs.py                  # POST /jobs (upload), GET /jobs/{id}, list, cancel
        detections.py            # GET, PATCH, auto-approve
        redactions.py            # commit + download
        entities.py              # entity-types, regions
        auth.py                  # /login, OIDC callbacks, /me
        admin.py                 # users, audit, api-keys
    core/
      auth.py                    # JWT + OIDC verification + role/scope decorator
      audit.py                   # audit log writer (append-only)
      storage.py                 # BlobStore interface + local/S3/MinIO impls
      ws.py                      # WebSocket job-update broadcaster
    db/
      models.py                  # SQLAlchemy: Tenant, User, Job, Detection, ApiKey, AuditEvent
      session.py
      migrations/                # Alembic
    detection/
      pipeline.py                # orchestrates the three layers + calibrator
      schema.py                  # Detection, TextSpan dataclasses (shared)
      rules/
        presidio_engine.py
        recognizers/
          generic.py             # email, url, ip, mac, imei, dob
          financial.py           # cards (Luhn), iban, swift, aba, sort_code
          india.py               # aadhaar (Verhoeff), pan, voter_id, dl, gstin
          us.py                  # ssn, ein, passport, dl
          uk.py                  # nhs (mod-11), nino
          eu.py                  # vat, german tax id, french insee
          medical.py             # npi, icd-10, mrn (configurable)
        context_scorer.py        # YAML-driven boost/penalty rules
      ner/
        transformer.py           # GLiNER wrapper; CPU/GPU device selection
      llm/
        verifier.py              # Ollama client; trigger policy; batching; fallback
        prompts.py               # versioned prompt templates + few-shot examples
        schema.py                # JSON-mode output schema
      calibrator.py              # score normalization; overlap merging; bucket assignment
    parsing/
      pdf_parser.py              # PyMuPDF; produces DocumentModel
      image_parser.py            # Pillow + OpenCV preprocessing; pytesseract OCR
      document_model.py          # TextSpan, PageImage, DocumentModel dataclasses
      ocr_preprocessing.py       # deskew (Hough), CLAHE, denoise (fastNlMeansDenoising)
    redaction/
      applier.py                 # dispatcher: routes Detections by source to module
      pdf_text.py                # PyMuPDF redaction annotations + apply_redactions
      pdf_image.py               # render-modify-overlay; PyMuPDF insert_image
      image_raster.py            # raw JPG/PNG/TIFF redaction
      metadata.py                # PDF info+XMP scrub; image EXIF scrub; integrity check
      verifier.py                # post-redaction re-detect pass
    workers/
      celery_app.py
      tasks.py                   # detect_job, redact_job, expire_blobs
  alembic.ini
  pyproject.toml                 # uv-managed deps (replaces requirements.txt)
  cli.py                         # bootstrap commands: admin create, migrate, etc.
```

### 2.4 Module layout (client)

```
client/src/
  routes/
    upload/                      # /upload — file picker + job config
    jobs/                        # /jobs — list (filter by status/type)
    jobs.review/                 # /jobs/:id/review — HITL review view
    admin/users/
    admin/audit/
    auth/login/
  components/
    review/
      pdf-viewer.tsx             # PDF.js wrapper
      image-viewer.tsx           # canvas-based for raw images
      overlay-layer.tsx          # SVG overlay rendering Detections as rects
      detection-list.tsx         # virtualized list with filters + bulk ops
      detection-card.tsx         # per-detection sidebar entry
      keyboard-shortcuts.tsx     # j/k/a/r/e
    common/                      # existing shadcn primitives preserved
  hooks/
    use-job.ts
    use-detections.ts
    use-job-ws.ts                # WebSocket subscription
  lib/
    api.ts                       # generated from OpenAPI
    auth.ts                      # token storage + refresh
  state/
    review-state.ts              # selection, filters, optimistic updates
```

## 3. Detection engine

### 3.1 Three-layer pipeline

Each layer produces a list of `Detection` records (`text`, `bbox`, `page`, `entity_type`, `score`, `source_layer`, `evidence`). These feed a final calibrator pass.

```
INPUT TEXT + bboxes
  │
  ▼  Layer A — Deterministic rules (Presidio + custom recognizers, region-modular)
  │     Score = pattern weight + context boost/penalty (YAML-driven)
  │     All checksum-validatable IDs validated (no raw regex acceptance)
  │
  ▼  Layer B — Transformer NER
  │     Default: urchade/gliner_multi_pii-v1 (~250 MB; multilingual; runtime label list)
  │     Alternatives: lakshyakh93/deberta_finetuned_pii;
  │                   obi/deid_roberta_i2b2 (medical-heavy)
  │     Outputs: PERSON, ORG, LOCATION, ADDRESS, DATE, NRP, JOB_TITLE
  │
  ▼  Layer C — LLM verifier (selective)
  │     Fires only when:
  │       1. Layers A and B disagree (presence or type)
  │       2. Calibrator-pre score in [0.5, 0.8]
  │       3. Span is flagged "table-cell" by structural heuristic
  │     Default: phi-3.5-mini-instruct Q4_K_M (~2.4 GB) on CPU
  │     GPU: qwen2.5-7b-instruct Q4_K_M (~4.5 GB)
  │     Output: {is_pii, confirmed_type, confidence, reason} via Ollama JSON mode
  │     Batching: 10–20 spans per call; fallback to single-span on parse failure
  │     Fallback: if Ollama unreachable, skip layer (audit-logged)
  │
  ▼  Calibrator
        Per-source score normalization
        Overlap merge (longer span wins on containment; higher score on partial)
        Configurable deny-list (per-tenant YAML)
        Confidence buckets: HIGH ≥ 0.85 · MED 0.6–0.85 · LOW < 0.6
```

### 3.2 Layer A — Deterministic rules

Replaces the current Presidio setup with region-modular recognizers loaded by config. Coverage targets for V1:

| Region module | Entities | Notes |
|---|---|---|
| `generic` | EMAIL, URL, IP_ADDRESS (v4/v6), MAC_ADDRESS, IMEI, USERNAME, CRYPTO_WALLET, DATE_OF_BIRTH (context-bound) | Always-on default |
| `financial` | CREDIT_CARD (Luhn), IBAN (mod-97), SWIFT_BIC, ROUTING_NUMBER, SORT_CODE | Always-on default |
| `india` | AADHAAR_IN (Verhoeff), PAN_IN, VOTER_ID_IN, DRIVING_LICENSE_IN, GSTIN | Opt-in by tenant |
| `us` | SSN (invalid-range filter), EIN, PASSPORT, DRIVER_LICENSE (per-state) | Opt-in |
| `uk` | NHS (mod-11), NINO | Opt-in |
| `eu` | VAT, GERMAN_TAX_ID, FRENCH_INSEE | Opt-in |
| `medical` | NPI (Luhn variant), ICD-10, MRN (regex configurable per tenant) | Opt-in (off by default) |

**Mandatory checksum validation** for everything that has one. The current Aadhaar regex matches any 12 digits and is the primary source of false positives in the existing tool; Verhoeff validation is the highest-impact single fix.

**Context scorer** (`detection/rules/context_scorer.py`): YAML config of boost/penalty terms per entity type, evaluated within ±N tokens of the candidate. Replaces the current ad-hoc context list inside `_add_custom_recognizers`.

### 3.3 Layer B — Transformer NER

Replaces spaCy `en_core_web_md`. Default `urchade/gliner_multi_pii-v1` for two reasons: it's multilingual (handles transliterated names that the current setup misses), and it accepts a runtime label list (we can add custom NER types per tenant without retraining).

The NER wrapper exposes a single `recognize(text: str, labels: list[str]) -> list[Span]` interface. Spans are mapped back to PDF text-layer character offsets and bounding boxes using the parser's offset-to-bbox table.

Performance target: ≤ 200 ms per page on CPU at the default model size.

### 3.4 Layer C — LLM verifier

The single most consequential addition. Catches the failures that pure pattern + NER systems can't reason through:

- "Is `MANAS S` in cell 2 of a `Father Name | <value>` row a label fragment or the actual name?"
- "Is `2024-03-15` a date of birth or just an invoice date?"
- "Is `Apollo Hospitals` an organization (PII-relevant in some contexts) or a place reference?"

**Trigger policy** keeps verifier load to ~10–25% of detected spans. Triggers are **OR-combined** — the verifier fires for a span if any one of the following holds:

1. Layers A and B disagree (one fires, the other doesn't; or they fire with different types).
2. Pre-calibrator score in `[0.5, 0.8]`.
3. Span is structurally flagged "table-cell" (left-aligned label / right-aligned value detected by the parser).

**Prompt design** (`detection/llm/prompts.py`):
- Versioned prompt templates (each prompt has a semver tag; the version is recorded in the Detection's `evidence_jsonb`).
- Few-shot examples per ambiguity class (label-vs-value, name-vs-org, date-disambiguation).
- Multi-span batch prompt by default; single-span fallback on JSON parse failure.
- Output schema enforced via Ollama's `format: json`:
  ```json
  {
    "results": [
      {"span_id": "string", "is_pii": true, "confirmed_type": "PERSON",
       "confidence": 0.92, "reason": "string"}
    ]
  }
  ```

**Failure modes:**
- Ollama unreachable → verifier skipped; fallback to A+B scores; `evidence_jsonb` records `llm_skipped: "unreachable"`.
- JSON parse failure → fall back to single-span prompts; if those also fail, skip with `llm_skipped: "parse_error"`.
- The pipeline never blocks waiting on the LLM. A 30-second hard timeout per batch.

### 3.5 Calibrator

- Per-source normalization: rules-with-checksum → 0.95+; rules-without-checksum → use base score; NER → softmax (with optional isotonic calibration once a labeled set exists); LLM → trust verifier's confidence directly when present.
- Overlap merge: prefer longer span on containment; higher score on partial overlap; both kept (and surfaced for HITL) if neither dominates.
- Deny-list filtering: per-tenant YAML loaded at boot; replaces the current hardcoded `DENY_LIST` set.
- Confidence buckets drive the HITL UI's bulk auto-approve.

**`evidence_jsonb` recommended shape** (written by every layer; merged by the calibrator):

```json
{
  "rules": {
    "fired": true,
    "rule_name": "aadhaar_pattern",
    "raw_score": 0.85,
    "checksum_validated": true,
    "context_boost": 0.10
  },
  "ner": {
    "fired": true,
    "model": "urchade/gliner_multi_pii-v1",
    "raw_score": 0.78,
    "label": "PERSON"
  },
  "llm": {
    "fired": true,
    "skipped": null,                     // or "unreachable" | "parse_error"
    "model": "phi3.5:mini-instruct-q4_K_M",
    "prompt_version": "verifier@1.0.0",
    "is_pii": true,
    "confirmed_type": "PERSON",
    "confidence": 0.92,
    "reason": "label/value table cell; 'Father Name' label precedes value"
  },
  "calibrator": {
    "merged_from": ["det_a1b2", "det_c3d4"],
    "trigger_reasons": ["disagreement", "table_cell"]
  }
}
```

This shape is normative — every layer writes its slot; absent slots mean the layer didn't fire. The calibrator records which trigger(s) caused the LLM to run.

### 3.6 Custom keywords / regex (preserved + improved)

- Per-job (current behavior) **and** per-tenant (new). Tenant-level keywords always apply.
- Fuzzy: per-keyword threshold; phonetic matching (Soundex/Metaphone) added for transliterated names.
- Regex: sandboxed via the `regex` package's `timeout=` parameter; bad patterns can't hang a worker.

### 3.7 Bugs explicitly killed

- `_deduplicate_results` called twice in `hybrid_detector.py:191-192` → single calibrator pass.
- Overlap branch with `pass` (silently drops detections) at `hybrid_detector.py:454-456` → calibrator handles all overlap cases explicitly.
- Aadhaar regex matching any 12 digits → Verhoeff checksum validation.
- Hardcoded English-centric `DENY_LIST` → per-tenant YAML.
- Label-heuristic regex narrowly tuned to Indian student documents → table-structure detector + LLM verifier handles arbitrary label/value forms.

## 4. Document and image processing

### 4.1 Bugs in current image redaction

| Bug | Location | Fix |
|---|---|---|
| Re-OCRs against a flat term list rather than `Detection` objects | `ocr_redaction.py:process_image_with_ocr` | Per-image OCR feeds back into the detection pipeline; image redaction operates on the unified `Detection` model |
| Calls `doc.delete_image`, `doc.add_image_ref`, `page.replace_image` (none exist in PyMuPDF as used) | `ocr_redaction.py:legal_redact_pdf` image pass | Use `page.get_image_bbox(img)` + `page.insert_image(bbox, stream=new_bytes, overlay=True)` |
| Two-pass design corrupts the PDF cross-ref table | Same | One pass per page: text redactions applied, then images re-rendered and overlaid |
| `del_xml_metadata()` swallows AttributeError silently | `ocr_redaction.py:344-350` | Hard-fail; verify metadata empty post-save |
| OCR redaction draws white boxes over whole spans (over-redaction) | `process_image_with_ocr` | Use `image_to_data` per-word bboxes |
| No verification PII is actually gone post-redaction | All | Mandatory verification pass (§4.7) |

### 4.2 Pipeline stages (per document)

```
1. Ingest          — write upload to blob storage; create Job row; enqueue task
2. Parse           — produce a unified DocumentModel (§4.3)
3. Detect          — feed DocumentModel through detection pipeline (§3)
4. (Wait for HITL) — §5
5. Redact          — apply approved Detections to original
6. Verify          — re-extract & re-OCR; assert no Detection survives
7. Finalize        — scrub metadata; write redacted blob; return URL
```

Failure in any stage transitions the Job to `FAILED` with the stage tag.

### 4.3 Unified DocumentModel

Both PDFs and raw images parse into the same shape:

```python
@dataclass
class TextSpan:
    text: str
    page: int
    char_start: int
    char_end: int
    bbox: Bbox
    source: Literal["text_layer", "ocr"]
    ocr_confidence: float | None

@dataclass
class PageImage:
    page: int
    image_index: int
    xref: int | None                 # PDF xref; None for raw image input
    bbox: Bbox
    raw_bytes: bytes
    ocr_spans: list[TextSpan]

@dataclass
class DocumentModel:
    doc_id: UUID
    format: Literal["pdf", "image"]
    page_count: int
    text_spans: list[TextSpan]
    page_images: list[PageImage]
    page_pixmaps: dict[int, bytes]   # 200 DPI PNG renders for review UI
```

Detection runs once on a flat `text_spans` list. The redaction applier dispatches by `source` + container.

### 4.4 PDF parsing (`parsing/pdf_parser.py`)

- Use `page.get_text("dict", flags=TEXT_PRESERVE_WHITESPACE)` to keep span/line structure with bboxes from the start.
- For each `page.get_images(full=True)`:
  - Extract via `doc.extract_image(xref)`.
  - Render to OpenCV; preprocess (deskew via Hough; contrast via CLAHE; denoise via fastNlMeansDenoising).
  - OCR with `pytesseract.image_to_data(...)` — per-word bboxes.
  - Add OCR spans to `DocumentModel.page_images[i].ocr_spans`.
- Whole-page rasterization at 200 DPI for review UI; cached in object storage; only generated when HITL is enabled.

### 4.5 Raw image parsing (`parsing/image_parser.py`)

- Same OCR + preprocessing as §4.4.
- One image = one page (page 0).
- Multi-page TIFF → multi-page DocumentModel.
- Supported formats: JPG, PNG, TIFF (single + multi-page), HEIC (via `pillow-heif`), WebP.

### 4.6 Redaction appliers

Three modules, one per output surface, all consuming `list[Detection]`:

- **`redaction/pdf_text.py`** — for `Detection.span.source == "text_layer"`:
  - `page.add_redact_annot(rect, text=replace_text|"", fill=(0,0,0)|None)` per detection.
  - `page.apply_redactions(images=PDF_REDACT_IMAGE_NONE)` — explicitly do not let PyMuPDF touch images here.
- **`redaction/pdf_image.py`** — for OCR spans on embedded PDF images:
  - Render the image bytes; draw burn-in rectangles over each span's pixel bbox.
  - Re-encode (preserving original format where reasonable).
  - `page.replace_image(xref, stream=new_bytes)` if available, else wipe-and-overlay via redaction annotation + `page.insert_image(bbox, stream=new_bytes, overlay=True)`.
  - **Ordering with text redactions on the same page is critical** to avoid the cross-ref corruption bug that motivates the rewrite (§4.1). The applier follows this strict per-page sequence: (1) collect both text and image `Detection`s for the page; (2) call `page.add_redact_annot(...)` for every text-layer span; (3) call `page.add_redact_annot(...)` to wipe the underlying image-stream bytes for any image being replaced; (4) call `page.apply_redactions(images=PDF_REDACT_IMAGE_NONE)` **once**; (5) only after `apply_redactions` returns, call `page.replace_image` / `page.insert_image` to overlay the rendered redacted images. There is exactly one `apply_redactions` call per page.
- **`redaction/image_raster.py`** — for raw image inputs:
  - Burn-in rectangles in pixel space; re-encode to original format; written as the redacted artefact.

All three honor the `method` parameter (`full_redact`, `obfuscate`, `replace`) consistently. Replace mode uses Pillow text rasterization (proper font metrics) instead of `cv2.putText`.

### 4.7 Verification pass

After redaction:
1. Re-parse the output document into a fresh `DocumentModel`.
2. Re-run the detection pipeline using the approved spans as exact-text + bbox-overlap search targets.
3. If any approved span is still findable, fail the job with `VERIFY_FAILED` and write the surviving detections to the audit log.

Adds ~10–20% time per job; non-negotiable for the enterprise positioning.

### 4.8 Metadata scrubbing (`redaction/metadata.py`)

- **PDF:** clear `info` dict; `doc.del_xml_metadata()`; strip `/EmbeddedFiles`; strip `/JS`, `/JavaScript`; strip annotations not added by us.
- **Image:** clear EXIF (`Image.getexif().clear()`); strip identifying ICC profile if present.
- Post-write integrity check: re-open the redacted file; assert metadata is empty; assert no `/EmbeddedFiles` remain.

## 5. Job lifecycle, HITL review, persistence

### 5.1 Job state machine

```
PENDING ─► PARSING ─► DETECTING ─► AWAITING_REVIEW ─► REDACTING ─► VERIFYING ─► COMPLETE
                                                                                  │
                                                                                  ▼
                                                                              EXPIRED (TTL)

  any stage ─► FAILED (with stage + error)
  any stage ─► CANCELLED (user-initiated)
```

States persisted in `jobs.status`. Frontend subscribes to per-job updates via WebSocket (`/api/v1/ws/jobs/{id}`); no polling.

### 5.2 Database schema (Postgres)

Sketch — full DDL in Alembic migrations.

```
tenants
  id (uuid pk), name, settings_jsonb, created_at
  -- V1 ships with one "default" tenant; multi-tenant runtime is V2.

users
  id (uuid pk), email, hashed_password (nullable for OIDC-only),
  oidc_sub (nullable, unique), role (admin|reviewer|viewer),
  tenant_id (fk tenants), created_at, last_login_at

api_keys
  id (uuid pk), tenant_id (fk), name, hashed_key, scopes (text[]),
  created_by (fk users), created_at, revoked_at

jobs
  id (uuid pk), tenant_id (fk), created_by (fk users),
  status (enum), source_filename, source_blob_uri, source_sha256,
  source_format (pdf|image), page_count,
  redacted_blob_uri (nullable), redacted_sha256 (nullable),
  config_jsonb (regions, enabled_entities, method, replace_text,
    custom_keywords, match_mode, llm_enabled),
  error_jsonb (nullable: {stage, message, surviving_detections}),
  expires_at, created_at, updated_at

detections
  id (uuid pk), job_id (fk), entity_type, text, page, bbox_jsonb,
  source_layer (rules|ner|llm|keyword), score,
  confidence_bucket (high|med|low),
  evidence_jsonb (raw scores per layer, prompt version, rule name),
  approval_state (pending|approved|rejected|edited),
  approved_text (nullable, for "edited"),
  reviewed_by (fk users, nullable), reviewed_at (nullable)

audit_events
  id (uuid pk), tenant_id (fk),
  actor_id (fk users, nullable), actor_type (user|system|api_key),
  action, target_type, target_id,
  ip_address, user_agent, payload_jsonb, created_at
  -- append-only at DB level: revoke UPDATE/DELETE for app role
```

Indexes: `jobs(tenant_id, status, created_at)`, `detections(job_id, approval_state)`, `audit_events(tenant_id, created_at)`.

`tenant_id` columns exist from day one even though V1 runtime is single-tenant — V2 multi-tenant requires no historical data migration.

Soft deletion is **not** used. Originals/redactions purge from blob storage at `expires_at`; rows persist for audit with blob URIs nulled.

### 5.3 REST API surface (v1)

```
Auth
  POST   /api/v1/auth/login              email + password
  GET    /api/v1/auth/oidc/login         redirect to IdP
  GET    /api/v1/auth/oidc/callback
  GET    /api/v1/auth/me

Jobs
  POST   /api/v1/jobs                    multipart upload + config → 202 + job_id
  GET    /api/v1/jobs                    list (paginated; filter by status/created_by)
  GET    /api/v1/jobs/{id}               state + counts
  DELETE /api/v1/jobs/{id}               cancel or expire-immediately
  GET    /api/v1/jobs/{id}/preview/{page} signed URL to PNG of rendered page

Detections
  GET    /api/v1/jobs/{id}/detections    paginated; filter by type/page/state/bucket
  PATCH  /api/v1/jobs/{id}/detections    bulk approve/reject/edit
                                            body: {ids, action, edited_text?}
  POST   /api/v1/jobs/{id}/detections/auto-approve
                                            body: {min_score?, types?, layers?}

Redaction
  POST   /api/v1/jobs/{id}/commit        kicks REDACTING; requires zero pending
  GET    /api/v1/jobs/{id}/download      signed URL to redacted blob

Config
  GET    /api/v1/entity-types
  GET    /api/v1/regions

Admin
  GET    /api/v1/admin/audit             paginated; filterable; CSV export
  GET    /api/v1/admin/users
  POST   /api/v1/admin/users             invite
  POST   /api/v1/admin/api-keys

Real-time
  WS     /api/v1/ws/jobs/{id}            state + per-stage progress
```

All endpoints require auth (JWT bearer or `X-API-Key`). `tenant_id` is never accepted in the request — it derives from the auth principal.

### 5.4 Frontend HITL review

Routes:
- `/upload` — file picker, region/entity/method/keyword config.
- `/jobs` — list (filter, search).
- `/jobs/:id/review` — the HITL view.
- `/admin/users`, `/admin/audit`.
- `/auth/login` — local + OIDC paths.

`/jobs/:id/review` layout:

```
┌─────────────────────────────────────────────────────────────┐
│  Header: filename · pages · job state pill · "Commit" btn   │
├──────────────────────────────────┬──────────────────────────┤
│                                  │  Filters                 │
│       PDF/image viewer           │  [✓] HIGH  [✓] MED       │
│       (PDF.js for PDF,           │  [ ] LOW                 │
│        Canvas for images)        │  Type: [All ▾]           │
│                                  │  Layer: [All ▾]          │
│       Detection overlays:        │  Page: [All ▾]           │
│       red = pending              │                          │
│       green = approved           │  Detections (47)         │
│       grey = rejected            │  ┌───────────────────┐   │
│       amber = edited             │  │ PERSON  0.92  P1  │   │
│                                  │  │ "MANAS S"         │   │
│       Two-way sync between       │  │ rules+ner+llm     │   │
│       overlay and sidebar        │  │ [Approve][Reject] │   │
│                                  │  │ [Edit replacement]│   │
│                                  │  └───────────────────┘   │
│                                  │  Bulk:                   │
│                                  │  [Approve all HIGH]      │
│                                  │  [Reject DATE_TIME]      │
│                                  │  [Auto-approve >0.85]    │
└──────────────────────────────────┴──────────────────────────┘
```

Behaviors:
- Two-way sync between viewer overlay and sidebar list.
- Bulk operations: approve-all-by-type, reject-all-by-bucket, auto-approve-by-threshold.
- Per-detection edit replacement text.
- Commit gated until every detection is non-pending.
- Optimistic updates with WebSocket fan-out.
- Keyboard nav: `j/k` next/prev, `a` approve, `r` reject, `e` edit.

PDF rendering: PDF.js. Image rendering: `<canvas>`. Overlay: SVG sibling positioned over the page; rectangles transformed via the page viewport.

### 5.5 Confidence-bucket auto-approve

Calibrator's HIGH/MED/LOW buckets enable one-click "approve all HIGH" in the UI. Per-tenant config can declare types that auto-approve at upload time and skip `AWAITING_REVIEW` (e.g., always auto-approve EMAIL). This makes the HITL flow tunable from "review every span" to "review only what's uncertain."

### 5.6 Audit trail

Every state transition + every approval/rejection/edit writes an `audit_events` row. `audit_events` is append-only at the DB level (Postgres role grants prevent UPDATE/DELETE). Admin UI exposes paginated/filterable view with CSV export.

### 5.7 Retention and expiry

- Per-tenant `retention_hours` in `tenants.settings_jsonb` (default 24h).
- Celery beat task purges expired blobs and nulls URIs on the rows.
- Manual `DELETE /jobs/{id}` supported and audited.

**Detection-text purge at expiry (privacy-critical).** `detections.text` and `detections.approved_text` store the actual matched substrings — by definition, the PII the system was redacting. Persisting these indefinitely after the source blobs expire would defeat the privacy posture. At expiry, the purge task therefore also nulls `detections.text`, `detections.approved_text`, and `detections.bbox_jsonb`, while preserving `entity_type`, `score`, `confidence_bucket`, `source_layer`, and `evidence_jsonb` (with any free-text PII inside `evidence_jsonb.llm.reason` redacted to `[purged]`). Audit rows referencing the job retain `target_id` but never embed PII text in `payload_jsonb` — the audit writer must hash/redact PII in payloads at write time.

## 6. Deployment, auth, operations

### 6.1 Deployment

Single `docker-compose.yml` brings up: api, worker, beat, frontend, postgres, redis, ollama, nginx (TLS), optional minio.

`docker-compose.gpu.yml` overlay adds NVIDIA device reservations and switches Ollama to the 7B model.

```bash
# CPU mode
docker compose up -d

# GPU mode
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

V1 ships single-tenant only; multi-tenant runtime path is V2.

### 6.2 Configuration model

Three layers (most-specific wins):

1. **Environment variables** (`.env`/Compose) — runtime infra: `DATABASE_URL`, `REDIS_URL`, `OIDC_*`, `STORAGE_BACKEND`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `LLM_VERIFIER_ENABLED`, etc.
2. **Tenant settings** (DB JSONB) — `enabled_regions`, `enabled_entities`, `deny_list`, `default_method`, `retention_hours`, `auto_approve_types`, `llm_enabled`.
3. **Job overrides** (request body) — same fields, narrowed for one run.

`pydantic-settings` validates envs; Pydantic schemas validate tenant + job overrides. `app/config/defaults.yaml` ships sensible per-region defaults (`generic` + `financial` on by default; other regions opt-in).

### 6.3 Authentication

Three identity sources funnel into one `User` row:

1. **Local accounts** — Argon2-hashed password; bootstrap via `redactly admin create --email ... --password ...`. No public signup.
2. **OIDC** — generic OIDC client (Keycloak / Auth0 / Azure AD / Google / Okta). JIT user provisioning; `OIDC_ROLE_CLAIM` + `OIDC_ROLE_MAP` for role mapping.
3. **API keys** — scoped (`jobs:create`, `jobs:read`, `admin:audit`); `X-API-Key` header; never re-exposed after creation.

**OIDC role default:** when an OIDC login produces no claim that maps to a known role, the JIT-provisioned user receives the most-restrictive role — `viewer`. Admins can elevate via the admin UI. This default avoids the failure mode where a misconfigured `OIDC_ROLE_MAP` accidentally grants `admin` to anyone with a successful SSO session.

Sessions: JWT (15 min) + rotating refresh token. CSRF token on state-changing endpoints (cookie + header).

Roles: `admin`, `reviewer`, `viewer`. Authorization centralized in a `@require(role | scope)` decorator.

### 6.4 Storage backends

Pluggable (`STORAGE_BACKEND={local,s3,minio}`):

- **local** — `/var/lib/redactly/blobs/...`; default for on-prem.
- **s3** — AWS or any S3-compatible (Wasabi, Backblaze, R2). SSE-C supported.
- **minio** — bundled in Compose for "feels like S3" without external deps.

All three implement `BlobStore` (`put`, `get`, `delete`, `signed_url`).

### 6.5 Observability (V1, minimal)

- **Structured JSON logs** to stdout via `structlog`. Standard fields: `ts`, `level`, `service`, `tenant_id`, `job_id`, `user_id`, `request_id`, `event`.
- **Prometheus `/metrics`**:
  - `redactly_jobs_total{status, tenant}`
  - `redactly_job_duration_seconds{stage}`
  - `redactly_detections_total{layer, type}`
  - `redactly_llm_verifier_calls_total{result}`
  - `redactly_redaction_verify_failures_total` — any non-zero is Sev-1
- **OpenTelemetry hooks** present but no exporter is required at runtime. Bundled Grafana/Loki/Tempo overlay deferred to V2.

### 6.6 Security

- Containers run as non-root.
- TLS terminated at Nginx; API never plaintext in production.
- Secrets via Docker secrets / env-from-secrets-mount; `.env` only for dev.
- Upload limits: 100MB/file, 500MB/job (configurable).
- CSP, HSTS, X-Frame-Options, Referrer-Policy default-on at Nginx.
- Rate limiting: `/auth/login` 10/min/IP; `/jobs` 60/min/key (configurable).
- Dependency scanning configs ship in V1 (Dependabot, `pip-audit`, `npm audit`); CI itself is V2.
- `docs/security.md` carries a STRIDE threat model with mitigations.

### 6.7 Backup & recovery

- `ops/backup.sh` — `pg_dump` + blob-store snapshot → tarball with timestamp.
- `ops/restore.sh` — inverse.
- Documented in `README.md` "Operations." On-prem-friendly minimum.

## 7. Phasing

### 7.1 V1 — "Industry-grade core" (this spec)

Detection (3-layer hybrid + region modules + checksums + GLiNER + Phi-3.5-mini). Document & image processing (PDF + images, OCR, mandatory verification pass). Workflow (Postgres + Celery + WebSocket + HITL UI). Auth (local + OIDC + API keys). Single-tenant deployment via Docker Compose with optional GPU overlay. Structured logging + basic Prometheus metrics. Backup/restore scripts.

### 7.2 V2 — "Office + multi-tenant"

DOCX/XLSX/PPTX support. Multi-tenant runtime (path/subdomain routing, per-tenant quotas, RLS policies). Helm chart + K8s manifests. Bundled observability stack overlay. Tests + CI/CD pipelines.

### 7.3 V3 — "Discovery platform"

Email formats (EML/MSG) with attachment recursion. Plain-text/CSV/JSON/XML formats. Storage connectors (S3 sweeps, SharePoint/OneDrive, Gmail/Outlook). Background discovery jobs surfacing PII findings as a dashboard. Public Python + TS SDKs.

### 7.4 Migration approach

This is a rewrite, not an incremental refactor. The current V1 codebase is small (~6 Python files, one frontend page); the design changes the surface area dramatically (Flask→FastAPI, no DB→Postgres, sync→Celery, no auth→OIDC, no review→HITL).

- Build the new server at `server/` (replacing the existing tree in a single PR sequence rather than parallel-existing).
- Rewrite the frontend in the same `client/` directory; preserve the shadcn UI primitives + Tailwind config; replace routing, state, page tree.
- No user-data migration needed — current V1 has no persistence.
- The Compose entrypoint flips from V1 to V2 in one PR; legacy `start.sh` removed.

## 8. Open questions and risks

- **Model licenses.** GLiNER is Apache 2.0; Phi-3.5 is MIT; Qwen 2.5 is Apache 2.0 — all permissive for commercial use. To be re-verified at implementation time.
- **OCR quality on low-resolution scans.** Mitigated by preprocessing (deskew + CLAHE + denoise) but OCR remains the weakest link in detection recall on poor-quality inputs. Acceptable for V1; consider a stronger OCR (PaddleOCR, EasyOCR) in V2 if measurable issues surface.
- **LLM verifier latency under load.** Single Ollama instance is the bottleneck. Mitigated by batching and the trigger policy (10–25% of spans). If insufficient, V1.x adds Ollama horizontal scaling via a shared model cache.
- **PyMuPDF API stability.** `page.replace_image` and related image-stream methods have shifted across versions. Pin a specific PyMuPDF release; document the supported version in `pyproject.toml` and `README.md`.
- **No tests in V1.** Per project owner. Verification pass partly compensates by catching post-redaction regressions at runtime, but absence of unit/integration tests is the largest single risk to long-term correctness. V2 plan must include test backfill before further detection-engine changes.

## 9. Decision log

- **Scope (C+D, privacy-first local).** Fully self-hostable; no cloud-API data path; supports both SaaS and internal-tool deployments off one binary.
- **V1 docs (PDF + raw images only).** Office/email/connectors deferred.
- **Detection: 3-layer hybrid (rules + transformer NER + selective LLM verifier).** Rejected pure-rules (loses contextual correctness) and pure-LLM (latency/cost on CPU; calibration difficulty).
- **HITL workflow (B).** Detect → Review → Approve → Redact, with bulk + auto-approve helpers. Plain auto-redact rejected as insufficient for enterprise audit requirements.
- **Hardware floor: CPU; GPU recommended.** Two-tier model loadout via Compose overlay.
- **V1 is single-tenant only at runtime.** Schema supports multi-tenant; runtime path comes in V2.
- **No bundled observability stack in V1.** Cheap built-ins only (JSON logs + `/metrics`).
- **No tests in V1.** Per project owner.
