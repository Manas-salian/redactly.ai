# Document Parsing Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the document-parsing subsystem — `DocumentModel` dataclasses, PDF parser (text-layer + embedded-image OCR), raw-image parser (JPG/PNG/TIFF/HEIC/WebP with multi-page TIFF), OCR preprocessing (deskew/CLAHE/denoise), DocumentModel persistence, and a Celery `parse_job` task — so subsequent plans (detection, redaction) have a stable input contract.

**Architecture:** Both PDFs and raw images parse into the *same* unified `DocumentModel` (see spec §4.3) so detection runs once on a flat `text_spans` list regardless of input format. Tesseract runs as a Linux system binary inside the worker container; PyMuPDF handles PDF extraction and rasterization; OpenCV + Pillow handle preprocessing. Parsed models are persisted as JSON to blob storage with per-page PNG rasters written alongside; the `jobs` row is updated with the new artefact URIs and the `page_count`. The new `parse_job` Celery task drives the pipeline asynchronously; a `python -m cli parse-file <path>` CLI command runs the same pipeline synchronously for offline smoke-checking.

**Tech Stack:** PyMuPDF (`fitz`) · pytesseract · Tesseract OCR · Pillow · pillow-heif · OpenCV (headless) · NumPy · Celery · existing Foundation stack (FastAPI, Postgres, Redis, blob storage).

**Reference docs:**
- Spec: `docs/superpowers/specs/2026-05-01-redactly-overhaul-design.md` (§2.3 module layout, §3 detection input, §4.1-4.5 parsing)
- Roadmap: `docs/superpowers/plans/2026-05-01-redactly-overhaul-roadmap.md` (Plan 2 acceptance gate)

**Acceptance gate** (from roadmap):
- Parsing a known PDF and a known image produces a `DocumentModel` with non-empty `text_spans` (mix of `text_layer` and `ocr` sources) and per-page-image OCR spans.
- 200 DPI page renders are written to blob storage at predictable keys.
- A multi-page TIFF parses into one `DocumentModel` with `page_count > 1`.
- The `parse_job` Celery task can be enqueued for an existing `Job` row, transitions the row from `PENDING` → `PARSING` → `AWAITING_REVIEW`, and writes both the parsed JSON and the per-page rasters to blob storage with URIs recorded on the row.

**Out of scope (later plans):**
- Detection engine → Plan 3
- Redaction → Plan 4
- Job-creation REST endpoint and `/jobs/{id}/preview/{page}` endpoint → Plan 5 (for V1 we exercise parse via CLI; the API plumbing waits)
- Office formats (DOCX/XLSX/PPTX) → V2
- Tests (unit + integration) → V2 backfill (project-owner directive)

**Prerequisites for the executor:**
- Foundation plan complete and merged or available on the working branch (`feature/foundation` if not yet merged).
- Docker stack already builds via Foundation's `Dockerfile`.
- `python -m cli` works (Foundation's Typer CLI).
- A small PDF and a small image are available locally for smoke checks. The plan provides 3 fixture-creator scripts so the executor doesn't need real-world files.

**Branch:** `feature/parsing` (already created from `feature/foundation`).

---

## File Structure

| Path | Responsibility |
|---|---|
| `server/pyproject.toml` | Add parsing deps: `pymupdf`, `pytesseract`, `pillow`, `pillow-heif`, `opencv-python-headless`, `numpy` |
| `server/Dockerfile` | Add system deps for OCR + image libs: `tesseract-ocr`, `tesseract-ocr-eng`, `libheif1`, `libgl1`, `libglib2.0-0` |
| `server/app/parsing/__init__.py` | Package marker (empty) |
| `server/app/parsing/document_model.py` | `Bbox`, `TextSpan`, `PageImage`, `DocumentModel` dataclasses + JSON (de)serialization |
| `server/app/parsing/ocr_preprocessing.py` | `deskew_hough`, `apply_clahe`, `denoise_fast_nl_means` — pure-function image transforms |
| `server/app/parsing/ocr.py` | `ocr_image(...)` — wraps `pytesseract.image_to_data` and produces `TextSpan` rows in pixel coords |
| `server/app/parsing/pdf_parser.py` | `parse_pdf(blob_bytes) -> DocumentModel` — text-layer extraction + per-image OCR + per-page raster |
| `server/app/parsing/image_parser.py` | `parse_image(blob_bytes, mime) -> DocumentModel` — single-page or multi-page TIFF; handles HEIC/WebP via Pillow |
| `server/app/parsing/parser.py` | `parse(blob_bytes, format, mime) -> DocumentModel` — top-level dispatch by format |
| `server/app/parsing/persistence.py` | `save_document_model(...)`, `load_document_model(...)`, `save_page_renders(...)` — `BlobStore`-backed |
| `server/app/db/models.py` (modify) | Add `parsed_document_uri` column to `jobs` (TEXT nullable) |
| `server/app/db/migrations/versions/0002_jobs_parsed_uri.py` | Alembic migration adding the column |
| `server/app/workers/tasks.py` (modify) | Add `parse_job(job_id)` Celery task |
| `server/app/workers/job_state.py` | Tiny helper module — `transition_job(db, job_id, to_status, error=None)` (centralizes state-machine validation; reused by detection/redaction in later plans) |
| `server/cli.py` (modify) | Add `parse-file <path>` and `enqueue-parse <job_id>` subcommands |
| `server/fixtures/sample.pdf` | Generated by a one-line PyMuPDF script committed in Task 13 |
| `server/fixtures/sample.png` | Generated by Pillow in same task |
| `server/fixtures/sample_multipage.tiff` | Generated by Pillow in same task |
| `docs/architecture.md` (modify) | Add a paragraph + diagram-fragment for the parsing pipeline |
| `docs/contributing.md` (modify) | Add a "Working with parsed documents" recipe |
| `CHANGELOG.md` (modify) | Add `[0.3.0-parsing]` section |

---

## Task 1: Add system + Python parsing dependencies

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `server/Dockerfile`

- [ ] **Step 1: Add Python deps to `server/pyproject.toml`**

In the `dependencies = [...]` array, append (preserve alphabetical-ish grouping; place near existing imaging deps if any):

```toml
    "pymupdf>=1.25,<1.26",
    "pytesseract>=0.3.13,<0.4",
    "pillow>=11.0,<12",
    "pillow-heif>=0.20,<0.21",
    "opencv-python-headless>=4.10,<5",
    "numpy>=2.1,<3",
```

- [ ] **Step 2: Add system deps to `server/Dockerfile`**

Find the existing `apt-get install -y --no-install-recommends \` block. Append these packages (one per line, with the trailing `\`):

```
    tesseract-ocr \
    tesseract-ocr-eng \
    libheif1 \
    libgl1 \
    libglib2.0-0 \
```

- [ ] **Step 3: Lock the new deps**

```bash
cd server && uv lock && cd ..
```

Expected: `server/uv.lock` updates without conflicts. Inspect the diff briefly — `pymupdf`, `pytesseract`, `pillow`, `pillow-heif`, `opencv-python-headless`, `numpy` should all appear.

- [ ] **Step 4: Smoke-test imports outside Docker (note: pytesseract import works without a tesseract binary; the binary is exercised in Task 4)**

```bash
cd server && uv sync && uv run python -c "
import fitz
import pytesseract
import PIL
import pillow_heif
import cv2
import numpy
print('ok')
" && cd ..
```

Expected: `ok`. If `pillow_heif` fails on Windows, that's OK — it'll work in the Linux Docker container; just note it as a concern in the report.

- [ ] **Step 5: Commit**

```bash
git add server/pyproject.toml server/uv.lock server/Dockerfile
git commit -m "feat(parsing): add OCR + imaging deps (PyMuPDF, Tesseract, Pillow, OpenCV)"
```

---

## Task 2: `DocumentModel` dataclasses + JSON serialization

**Files:**
- Create: `server/app/parsing/__init__.py`
- Create: `server/app/parsing/document_model.py`

- [ ] **Step 1: Create empty package marker**

```bash
touch server/app/parsing/__init__.py
```

- [ ] **Step 2: Write `server/app/parsing/document_model.py`**

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Bbox:
    """Axis-aligned bounding box. Coordinate space is recorded by the consumer
    (PDF user-space points for `text_layer` spans; image pixels for `ocr` spans
    on raster page images)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def width(self) -> float:
        return self.x1 - self.x0

    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class TextSpan:
    text: str
    page: int  # 0-indexed
    char_start: int  # offset within page-concatenated text
    char_end: int
    bbox: Bbox
    source: Literal["text_layer", "ocr"]
    ocr_confidence: float | None = None  # filled only for source="ocr"


@dataclass
class PageImage:
    """A raster image embedded in a PDF page, or an entire raw-image input.

    For PDF inputs, `xref` is the PyMuPDF xref of the embedded image and `bbox`
    is its placement on the page in user-space points.

    For raw-image inputs (JPG, PNG, TIFF, HEIC, WebP), `xref` is None, the
    `bbox` is the full image extent in pixels, and `page` matches the
    DocumentModel page index (0 for single-page formats; 0..N-1 for multi-page
    TIFF).
    """

    page: int
    image_index: int  # nth image on the page (0 for raw-image input)
    xref: int | None
    bbox: Bbox
    raw_bytes: bytes
    ocr_spans: list[TextSpan] = field(default_factory=list)


@dataclass
class DocumentModel:
    doc_id: UUID
    format: Literal["pdf", "image"]
    page_count: int
    text_spans: list[TextSpan] = field(default_factory=list)
    page_images: list[PageImage] = field(default_factory=list)
    page_render_uris: dict[int, str] = field(default_factory=dict)
    # `page_pixmaps` from spec §4.3 is materialized as `page_render_uris` here:
    # the bytes live in blob storage; this dict stores their URIs keyed by page.

    @classmethod
    def new(cls, format: Literal["pdf", "image"], page_count: int) -> DocumentModel:
        return cls(doc_id=uuid4(), format=format, page_count=page_count)


def to_json(model: DocumentModel) -> str:
    """Serialize a DocumentModel to JSON. PageImage.raw_bytes is dropped — it
    lives in blob storage; only the metadata round-trips."""
    return json.dumps(_to_dict(model), separators=(",", ":"))


def from_json(payload: str) -> DocumentModel:
    return _from_dict(json.loads(payload))


def _to_dict(model: DocumentModel) -> dict:
    d = asdict(model)
    d["doc_id"] = str(model.doc_id)
    for img in d["page_images"]:
        img.pop("raw_bytes", None)
    d["page_render_uris"] = {str(k): v for k, v in model.page_render_uris.items()}
    return d


def _from_dict(d: dict) -> DocumentModel:
    text_spans = [
        TextSpan(
            text=s["text"],
            page=s["page"],
            char_start=s["char_start"],
            char_end=s["char_end"],
            bbox=Bbox(**s["bbox"]),
            source=s["source"],
            ocr_confidence=s.get("ocr_confidence"),
        )
        for s in d.get("text_spans", [])
    ]
    page_images = [
        PageImage(
            page=p["page"],
            image_index=p["image_index"],
            xref=p.get("xref"),
            bbox=Bbox(**p["bbox"]),
            raw_bytes=b"",
            ocr_spans=[
                TextSpan(
                    text=s["text"],
                    page=s["page"],
                    char_start=s["char_start"],
                    char_end=s["char_end"],
                    bbox=Bbox(**s["bbox"]),
                    source=s["source"],
                    ocr_confidence=s.get("ocr_confidence"),
                )
                for s in p.get("ocr_spans", [])
            ],
        )
        for p in d.get("page_images", [])
    ]
    return DocumentModel(
        doc_id=UUID(d["doc_id"]),
        format=d["format"],
        page_count=d["page_count"],
        text_spans=text_spans,
        page_images=page_images,
        page_render_uris={int(k): v for k, v in d.get("page_render_uris", {}).items()},
    )
```

- [ ] **Step 3: Smoke-test the module loads and round-trips**

```bash
cd server && uv run python -c "
from app.parsing.document_model import (
    Bbox, TextSpan, PageImage, DocumentModel, to_json, from_json
)
m = DocumentModel.new('pdf', 2)
m.text_spans.append(TextSpan(
    text='Hello', page=0, char_start=0, char_end=5,
    bbox=Bbox(0, 0, 30, 12), source='text_layer'
))
m.page_render_uris[0] = 'blob+local://renders/abc/page-0.png'
s = to_json(m)
m2 = from_json(s)
assert m2.format == 'pdf'
assert m2.page_count == 2
assert m2.text_spans[0].text == 'Hello'
assert m2.text_spans[0].bbox.width() == 30
assert m2.page_render_uris[0].startswith('blob+local://')
print('ok')
" && cd ..
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add server/app/parsing/__init__.py server/app/parsing/document_model.py
git commit -m "feat(parsing): add DocumentModel dataclasses + JSON serialization"
```

---

## Task 3: OCR preprocessing transforms

**Files:**
- Create: `server/app/parsing/ocr_preprocessing.py`

- [ ] **Step 1: Write `server/app/parsing/ocr_preprocessing.py`**

```python
"""OCR preprocessing transforms.

Pure functions over OpenCV BGR ndarrays. Run in this order on raw page or
embedded-image rasters before handing them to Tesseract:

    1. deskew_hough     — corrects tilt within ±15°
    2. apply_clahe      — local-contrast normalization
    3. denoise_fast_nl_means — speckle/jpeg-noise removal

Each transform returns a NEW ndarray; inputs are not mutated.
"""

from __future__ import annotations

import cv2
import numpy as np


def deskew_hough(image: np.ndarray, max_angle_deg: float = 15.0) -> np.ndarray:
    """Detect text-line orientation via the Hough transform on edge-detected
    grayscale, then rotate to deskew. Returns the input unmodified if no
    confident skew angle is detected within ±max_angle_deg.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=200)
    if lines is None:
        return image

    angles_deg: list[float] = []
    for rho_theta in lines:
        rho, theta = rho_theta[0]
        angle = (theta * 180.0 / np.pi) - 90.0
        if -max_angle_deg <= angle <= max_angle_deg:
            angles_deg.append(angle)

    if not angles_deg:
        return image

    median_angle = float(np.median(angles_deg))
    if abs(median_angle) < 0.25:
        return image

    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    rotation = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    return cv2.warpAffine(
        image, rotation, (w, h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def apply_clahe(image: np.ndarray, clip_limit: float = 2.0, tile_grid_size: tuple[int, int] = (8, 8)) -> np.ndarray:
    """CLAHE (Contrast Limited Adaptive Histogram Equalization) on the luminance
    channel. Improves OCR on photos and scanned documents with uneven lighting.
    """
    if image.ndim == 2:
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
        return clahe.apply(image)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    l = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def denoise_fast_nl_means(image: np.ndarray, h_strength: int = 7) -> np.ndarray:
    """Non-local-means denoising. Slower than bilateral filtering but preserves
    text edges better. h_strength of 7-10 is reasonable for scanned docs.
    """
    if image.ndim == 2:
        return cv2.fastNlMeansDenoising(image, h=h_strength, templateWindowSize=7, searchWindowSize=21)
    return cv2.fastNlMeansDenoisingColored(image, h=h_strength, hColor=h_strength, templateWindowSize=7, searchWindowSize=21)


def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    """Run the standard preprocessing chain. Each step is independent and can
    be skipped by callers that want only some transforms; this is the
    convenience entrypoint."""
    return denoise_fast_nl_means(apply_clahe(deskew_hough(image)))
```

- [ ] **Step 2: Smoke-test the chain runs on a synthetic image**

```bash
cd server && uv run python -c "
import numpy as np
from app.parsing.ocr_preprocessing import deskew_hough, apply_clahe, denoise_fast_nl_means, preprocess_for_ocr

img = np.full((200, 200, 3), 200, dtype=np.uint8)
img[80:120, 20:180] = 30  # a dark band

a = deskew_hough(img)
b = apply_clahe(a)
c = denoise_fast_nl_means(b)
d = preprocess_for_ocr(img)

assert a.shape == img.shape
assert b.shape == img.shape
assert c.shape == img.shape
assert d.shape == img.shape
print('ok', a.dtype, b.dtype, c.dtype, d.dtype)
" && cd ..
```

Expected: `ok uint8 uint8 uint8 uint8`.

- [ ] **Step 3: Commit**

```bash
git add server/app/parsing/ocr_preprocessing.py
git commit -m "feat(parsing): add OCR preprocessing (deskew + CLAHE + denoise)"
```

---

## Task 4: OCR wrapper around pytesseract

**Files:**
- Create: `server/app/parsing/ocr.py`

This wraps `pytesseract.image_to_data` and produces `TextSpan`s with pixel-coordinate bboxes. It's the single place that knows about Tesseract — pdf_parser and image_parser both call `ocr_image`.

- [ ] **Step 1: Write `server/app/parsing/ocr.py`**

```python
"""Tesseract OCR wrapper.

`ocr_image(...)` is the one place that talks to pytesseract. Both pdf_parser
and image_parser call it. Returns TextSpans in pixel coordinates; the caller
is responsible for converting to PDF user-space if applicable.
"""

from __future__ import annotations

import numpy as np
import pytesseract

from app.parsing.document_model import Bbox, TextSpan
from app.parsing.ocr_preprocessing import preprocess_for_ocr

# --oem 3 = default LSTM engine; --psm 6 = assume a uniform block of text.
# preserve_interword_spaces helps line-reconstruction downstream.
_TESSERACT_CONFIG = "--oem 3 --psm 6 -c preserve_interword_spaces=1"


def ocr_image(
    image_bgr: np.ndarray,
    *,
    page: int,
    char_offset_start: int = 0,
    preprocess: bool = True,
    min_confidence: int = 30,
) -> tuple[list[TextSpan], int]:
    """Run Tesseract on a BGR image and return per-word TextSpans + the next
    char offset.

    Tesseract's word confidence is 0-100 (or -1 for no result); we drop spans
    below `min_confidence` (default 30 — generous; later plans can tighten).
    """
    img = preprocess_for_ocr(image_bgr) if preprocess else image_bgr

    data = pytesseract.image_to_data(
        img, config=_TESSERACT_CONFIG, output_type=pytesseract.Output.DICT
    )

    spans: list[TextSpan] = []
    cursor = char_offset_start

    n = len(data["text"])
    for i in range(n):
        word = data["text"][i] or ""
        word = word.strip()
        if not word:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError):
            conf = -1
        if conf < min_confidence:
            continue

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])

        spans.append(
            TextSpan(
                text=word,
                page=page,
                char_start=cursor,
                char_end=cursor + len(word),
                bbox=Bbox(x, y, x + w, y + h),
                source="ocr",
                ocr_confidence=conf / 100.0,
            )
        )
        cursor += len(word) + 1  # +1 accounts for the implicit space between words

    return spans, cursor
```

- [ ] **Step 2: Smoke-test against a synthetic image with rendered text**

This requires the Tesseract binary. On a host that doesn't have it (likely Windows), this step will fail — that's OK. The Docker image (built in Task 1) does have it. So the verification path is via Docker:

```bash
docker run --rm -v "$(pwd)/server:/app" -w /app redactly-server:foundation bash -c '
uv run python -c "
import numpy as np
import cv2
from app.parsing.ocr import ocr_image

# Synthesize a 400x100 white image with the word HELLO drawn in black
img = np.full((100, 400, 3), 255, dtype=np.uint8)
cv2.putText(img, \"HELLO WORLD\", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0,0,0), 3)

spans, end = ocr_image(img, page=0)
texts = [s.text.upper() for s in spans]
print(\"texts:\", texts)
assert any(\"HELLO\" in t for t in texts), texts
assert end > 0
print(\"ok\")
"'
```

Expected: stdout contains `ok` and a `texts:` line listing at least `HELLO`. Tesseract is finicky with synthesized text at low contrast; if `HELLO WORLD` doesn't perfectly OCR, accept it as long as one of the words is present.

If the `redactly-server:foundation` image isn't built (or is stale), rebuild first:

```bash
docker build -t redactly-server:foundation -f server/Dockerfile server/
```

- [ ] **Step 3: Commit**

```bash
git add server/app/parsing/ocr.py
git commit -m "feat(parsing): add Tesseract OCR wrapper with per-word bboxes"
```

---

## Task 5: PDF parser

**Files:**
- Create: `server/app/parsing/pdf_parser.py`

- [ ] **Step 1: Write `server/app/parsing/pdf_parser.py`**

```python
"""PDF parser.

Produces a DocumentModel with:
- Native text-layer spans (one per word) with bboxes in PDF user-space points.
- For each embedded image, OCR'd TextSpans in pixel coordinates of the image.
- A 200 DPI raster of every page (raw bytes; persistence is the caller's job).
"""

from __future__ import annotations

import io
from typing import Iterator

import cv2
import fitz
import numpy as np
from PIL import Image

from app.parsing.document_model import Bbox, DocumentModel, PageImage, TextSpan
from app.parsing.ocr import ocr_image

PAGE_RENDER_DPI = 200


def parse_pdf(blob_bytes: bytes) -> tuple[DocumentModel, dict[int, bytes]]:
    """Return (DocumentModel, page_renders) where page_renders is a dict
    mapping page index → PNG bytes of that page rendered at PAGE_RENDER_DPI.
    The caller persists the renders separately and fills in
    `model.page_render_uris`.
    """
    doc = fitz.open(stream=blob_bytes, filetype="pdf")
    try:
        model = DocumentModel.new(format="pdf", page_count=doc.page_count)
        page_renders: dict[int, bytes] = {}
        text_offset_per_page: list[int] = [0] * doc.page_count

        for page_idx in range(doc.page_count):
            page = doc[page_idx]

            # 1. Native text-layer extraction.
            offset = 0
            for word in page.get_text("words"):
                # PyMuPDF "words" tuples: (x0, y0, x1, y1, "text", block, line, word_no)
                x0, y0, x1, y1, text, _, _, _ = word
                if not text:
                    continue
                model.text_spans.append(
                    TextSpan(
                        text=text,
                        page=page_idx,
                        char_start=offset,
                        char_end=offset + len(text),
                        bbox=Bbox(float(x0), float(y0), float(x1), float(y1)),
                        source="text_layer",
                    )
                )
                offset += len(text) + 1
            text_offset_per_page[page_idx] = offset

            # 2. Per-image OCR.
            for image_index, img_info in enumerate(page.get_images(full=True)):
                xref = int(img_info[0])
                try:
                    extracted = doc.extract_image(xref)
                except Exception:  # noqa: BLE001 — PyMuPDF raises broadly here
                    continue
                img_bytes = extracted.get("image")
                if not img_bytes:
                    continue

                # PyMuPDF gives us the image bytes; we need the image's bbox on
                # the page. `page.get_image_bbox` accepts the image-info tuple.
                try:
                    page_bbox = page.get_image_bbox(img_info)
                except ValueError:
                    continue

                cv = _decode_to_bgr(img_bytes)
                if cv is None:
                    continue

                ocr_spans, _ = ocr_image(
                    cv,
                    page=page_idx,
                    char_offset_start=offset,
                )
                offset += sum(len(s.text) + 1 for s in ocr_spans)

                model.page_images.append(
                    PageImage(
                        page=page_idx,
                        image_index=image_index,
                        xref=xref,
                        bbox=Bbox(
                            float(page_bbox.x0), float(page_bbox.y0),
                            float(page_bbox.x1), float(page_bbox.y1),
                        ),
                        raw_bytes=img_bytes,
                        ocr_spans=ocr_spans,
                    )
                )
                # Surface OCR spans in the flat text_spans list too — detection
                # runs over a single list per spec §4.3.
                model.text_spans.extend(ocr_spans)

            # 3. 200 DPI page render for the review UI.
            zoom = PAGE_RENDER_DPI / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            page_renders[page_idx] = pix.tobytes("png")

        return model, page_renders
    finally:
        doc.close()


def _decode_to_bgr(image_bytes: bytes) -> np.ndarray | None:
    """Decode arbitrary image bytes to an OpenCV BGR ndarray. Returns None if
    Pillow can't decode the input."""
    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    arr = np.asarray(pil)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def iter_page_renders(renders: dict[int, bytes]) -> Iterator[tuple[int, bytes]]:
    """Iterate page_index → PNG bytes in page order."""
    for k in sorted(renders.keys()):
        yield k, renders[k]
```

- [ ] **Step 2: Smoke-test with a synthesized PDF**

This uses PyMuPDF to *create* a known PDF in-memory, then parse it back. Runs without external fixtures:

```bash
cd server && uv run python -c "
import fitz
from app.parsing.pdf_parser import parse_pdf

# Build a tiny 1-page PDF with native text.
doc = fitz.open()
page = doc.new_page(width=400, height=200)
page.insert_text((50, 100), 'Hello redactly')
pdf_bytes = doc.tobytes()
doc.close()

model, renders = parse_pdf(pdf_bytes)
assert model.page_count == 1, model.page_count
texts = [s.text for s in model.text_spans if s.source == 'text_layer']
print('texts:', texts)
assert 'Hello' in texts and 'redactly' in texts, texts
assert 0 in renders and renders[0][:8].startswith(b'\\x89PNG')
print('ok')
" && cd ..
```

Expected: `texts: ['Hello', 'redactly']` (or similar — PyMuPDF may split differently) and `ok`.

- [ ] **Step 3: Commit**

```bash
git add server/app/parsing/pdf_parser.py
git commit -m "feat(parsing): add PDF parser (text-layer + per-image OCR + 200 DPI renders)"
```

---

## Task 6: Image parser (raw inputs, multi-page TIFF)

**Files:**
- Create: `server/app/parsing/image_parser.py`

- [ ] **Step 1: Write `server/app/parsing/image_parser.py`**

```python
"""Raw-image parser.

Handles JPG, PNG, TIFF (single + multi-page), HEIC (via pillow-heif), and WebP.
Each page becomes one PageImage + one entry in page_render_uris. OCR runs on
every page; the OCR spans are added to both PageImage.ocr_spans and the flat
text_spans list (per spec §4.3).
"""

from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image, ImageSequence

# Register HEIC/HEIF support in Pillow.
try:
    import pillow_heif  # type: ignore[import-not-found]

    pillow_heif.register_heif_opener()
except ImportError:  # pillow-heif optional on systems missing libheif
    pass

from app.parsing.document_model import Bbox, DocumentModel, PageImage, TextSpan
from app.parsing.ocr import ocr_image


def parse_image(blob_bytes: bytes, mime: str | None = None) -> tuple[DocumentModel, dict[int, bytes]]:
    """Parse a raw image input. `mime` is advisory; format is auto-detected by
    Pillow. Returns (DocumentModel, page_renders) like parse_pdf.
    """
    pil = Image.open(io.BytesIO(blob_bytes))
    pil.load()

    # Multi-page TIFF (and animated formats) expose >1 frame via ImageSequence.
    pages: list[Image.Image] = []
    for frame in ImageSequence.Iterator(pil):
        pages.append(frame.convert("RGB").copy())

    page_count = len(pages)
    model = DocumentModel.new(format="image", page_count=page_count)
    page_renders: dict[int, bytes] = {}

    cursor = 0
    for page_idx, page in enumerate(pages):
        bgr = cv2.cvtColor(np.asarray(page), cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]

        ocr_spans, cursor = ocr_image(bgr, page=page_idx, char_offset_start=cursor)

        # Re-encode the page as PNG for the review-UI raster.
        png_buf = io.BytesIO()
        page.save(png_buf, format="PNG")
        png_bytes = png_buf.getvalue()

        model.page_images.append(
            PageImage(
                page=page_idx,
                image_index=0,
                xref=None,
                bbox=Bbox(0.0, 0.0, float(w), float(h)),
                raw_bytes=blob_bytes if page_count == 1 else png_bytes,
                ocr_spans=ocr_spans,
            )
        )
        model.text_spans.extend(ocr_spans)
        page_renders[page_idx] = png_bytes

    pil.close()
    return model, page_renders
```

- [ ] **Step 2: Smoke-test with synthesized images**

This needs Tesseract — run via Docker:

```bash
docker run --rm -v "$(pwd)/server:/app" -w /app redactly-server:foundation bash -c '
uv run python -c "
import io
import cv2
import numpy as np
from PIL import Image
from app.parsing.image_parser import parse_image

# Single-page PNG with rendered text
img = np.full((150, 600, 3), 255, dtype=np.uint8)
cv2.putText(img, \"PARSE ME PLEASE\", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 2, (0,0,0), 4)
buf = io.BytesIO()
Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(buf, format=\"PNG\")

model, renders = parse_image(buf.getvalue(), mime=\"image/png\")
assert model.format == \"image\"
assert model.page_count == 1
texts = [s.text.upper() for s in model.text_spans]
print(\"png texts:\", texts)
assert any(\"PARSE\" in t for t in texts), texts

# Multi-page TIFF
p1 = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
img2 = np.full((150, 600, 3), 255, dtype=np.uint8)
cv2.putText(img2, \"PAGE TWO\", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 2, (0,0,0), 4)
p2 = Image.fromarray(cv2.cvtColor(img2, cv2.COLOR_BGR2RGB))
tiff_buf = io.BytesIO()
p1.save(tiff_buf, format=\"TIFF\", save_all=True, append_images=[p2])

model2, renders2 = parse_image(tiff_buf.getvalue(), mime=\"image/tiff\")
assert model2.page_count == 2, model2.page_count
print(\"tiff page_count:\", model2.page_count)
print(\"ok\")
"'
```

Expected: stdout shows `png texts:` containing PARSE (or similar OCR'd words), `tiff page_count: 2`, and `ok`.

- [ ] **Step 3: Commit**

```bash
git add server/app/parsing/image_parser.py
git commit -m "feat(parsing): add image parser (PNG/JPG/TIFF/HEIC/WebP, multi-page TIFF)"
```

---

## Task 7: Top-level `parse(...)` dispatcher

**Files:**
- Create: `server/app/parsing/parser.py`

- [ ] **Step 1: Write `server/app/parsing/parser.py`**

```python
"""Top-level parser dispatcher.

Pick PDF or image parser based on the source format. Both paths return the
same (DocumentModel, page_renders) tuple shape.
"""

from __future__ import annotations

from typing import Literal

from app.parsing.document_model import DocumentModel
from app.parsing.image_parser import parse_image
from app.parsing.pdf_parser import parse_pdf

SourceFormat = Literal["pdf", "image"]


def parse(
    blob_bytes: bytes,
    source_format: SourceFormat,
    mime: str | None = None,
) -> tuple[DocumentModel, dict[int, bytes]]:
    if source_format == "pdf":
        return parse_pdf(blob_bytes)
    if source_format == "image":
        return parse_image(blob_bytes, mime=mime)
    raise ValueError(f"unsupported source_format: {source_format!r}")
```

- [ ] **Step 2: Smoke-test dispatch**

```bash
cd server && uv run python -c "
import fitz
from app.parsing.parser import parse

doc = fitz.open()
doc.new_page().insert_text((50, 100), 'Dispatch')
pdf_bytes = doc.tobytes()
doc.close()

model, renders = parse(pdf_bytes, 'pdf')
assert model.format == 'pdf'
print('dispatch ok')
" && cd ..
```

Expected: `dispatch ok`.

- [ ] **Step 3: Commit**

```bash
git add server/app/parsing/parser.py
git commit -m "feat(parsing): add top-level parse() dispatcher"
```

---

## Task 8: DocumentModel persistence + page-render upload

**Files:**
- Create: `server/app/parsing/persistence.py`

- [ ] **Step 1: Write `server/app/parsing/persistence.py`**

```python
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
```

- [ ] **Step 2: Smoke-test save + load round-trip**

```bash
cd server && uv run python -c "
from io import BytesIO
from pathlib import Path
import tempfile
from uuid import uuid4

from app.core.storage import LocalBlobStore
from app.parsing.document_model import Bbox, DocumentModel, TextSpan
from app.parsing.persistence import save_document_model, load_document_model

with tempfile.TemporaryDirectory() as tmp:
    store = LocalBlobStore(Path(tmp))
    job_id = uuid4()
    model = DocumentModel.new('image', 1)
    model.text_spans.append(TextSpan(
        text='Hi', page=0, char_start=0, char_end=2,
        bbox=Bbox(0,0,10,10), source='ocr', ocr_confidence=0.92,
    ))
    json_uri, render_uris = save_document_model(
        store, job_id, model, {0: b'\\x89PNG\\r\\n\\x1a\\n' + b'fakepng'},
    )
    print('json_uri:', json_uri)
    print('render_uris:', render_uris)
    loaded = load_document_model(store, job_id)
    assert loaded.text_spans[0].text == 'Hi'
    assert loaded.text_spans[0].ocr_confidence == 0.92
    assert loaded.page_render_uris[0].startswith('file://')
    print('ok')
" && cd ..
```

Expected: prints `ok` after the URIs.

- [ ] **Step 3: Commit**

```bash
git add server/app/parsing/persistence.py
git commit -m "feat(parsing): add DocumentModel persistence (JSON + page rasters)"
```

---

## Task 9: Add `parsed_document_uri` column to `jobs` + Alembic migration

**Files:**
- Modify: `server/app/db/models.py`
- Create: `server/app/db/migrations/versions/0002_jobs_parsed_uri.py`

- [ ] **Step 1: Add the column to the `Job` model**

In `server/app/db/models.py`, locate the `Job` class (`__tablename__ = "jobs"`). Add immediately after the existing `redacted_blob_uri` and `redacted_sha256` lines:

```python
    parsed_document_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
```

(`Text` is already imported at the top of the file.)

- [ ] **Step 2: Generate the migration**

We need a running Postgres for autogenerate. Foundation Task 7 used a temporary container; do the same:

```bash
docker run -d --name redactly-tmp-pg \
    -e POSTGRES_USER=redactly -e POSTGRES_PASSWORD=redactly -e POSTGRES_DB=redactly \
    -p 5432:5432 postgres:16-alpine

# Wait for ready
sleep 5

# Apply existing migrations to bring the temp DB to "head"
DATABASE_URL=postgresql+psycopg://redactly:redactly@localhost:5432/redactly \
    bash -c 'cd server && uv run alembic upgrade head'

# Autogenerate the new migration
DATABASE_URL=postgresql+psycopg://redactly:redactly@localhost:5432/redactly \
    bash -c 'cd server && uv run alembic revision --autogenerate -m "jobs.parsed_document_uri"'

docker rm -f redactly-tmp-pg
```

A new file appears in `server/app/db/migrations/versions/`. Rename it to `0002_jobs_parsed_uri.py` for stable ordering.

Inspect the generated file: it should contain a single `op.add_column('jobs', sa.Column('parsed_document_uri', sa.Text(), nullable=True))` in `upgrade()` and the inverse `op.drop_column` in `downgrade()`. If it includes anything else, something has drifted between the model and the prior migration — investigate before committing.

- [ ] **Step 3: Verify offline render**

```bash
DATABASE_URL=postgresql+psycopg://x:x@localhost:5432/x \
    bash -c 'cd server && uv run alembic upgrade head --sql 2>&1 | grep -A 2 parsed_document_uri'
```

Expected: SQL emits `ALTER TABLE jobs ADD COLUMN parsed_document_uri TEXT;` (or equivalent; column is nullable, no NOT NULL).

- [ ] **Step 4: Commit**

```bash
git add server/app/db/models.py server/app/db/migrations/versions/0002_jobs_parsed_uri.py
git commit -m "feat(parsing): add jobs.parsed_document_uri + migration 0002"
```

---

## Task 10: Job state-transition helper

**Files:**
- Create: `server/app/workers/job_state.py`

This is a tiny helper with one function. We extract it into its own module because Plans 3, 4, and 5 will all call it from worker code, and centralizing the validation rules avoids drift.

- [ ] **Step 1: Write `server/app/workers/job_state.py`**

```python
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
    JobStatus.PARSING: frozenset({JobStatus.DETECTING, JobStatus.FAILED, JobStatus.CANCELLED}),
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
```

- [ ] **Step 2: Smoke-test the import + transition rules**

```bash
cd server && uv run python -c "
from app.workers.job_state import _VALID_TRANSITIONS, IllegalTransition
from app.db.models import JobStatus

assert JobStatus.PARSING in _VALID_TRANSITIONS[JobStatus.PENDING]
assert JobStatus.AWAITING_REVIEW in _VALID_TRANSITIONS[JobStatus.DETECTING]
assert JobStatus.PENDING not in _VALID_TRANSITIONS.get(JobStatus.COMPLETE, set())
assert JobStatus.FAILED not in _VALID_TRANSITIONS.get(JobStatus.FAILED, set())
print('ok')
" && cd ..
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add server/app/workers/job_state.py
git commit -m "feat(parsing): add job state-transition helper"
```

---

## Task 11: `parse_job` Celery task

**Files:**
- Modify: `server/app/workers/tasks.py`

- [ ] **Step 1: Replace `server/app/workers/tasks.py`**

The Foundation `tasks.py` has only the placeholder `ping` task. Append the new task while preserving `ping`. The full new file:

```python
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
```

- [ ] **Step 2: Smoke-test the import + Celery registration**

```bash
cd server && uv run python -c "
from app.workers.tasks import parse_job, ping
from app.workers.celery_app import celery_app

names = set(celery_app.tasks.keys())
assert 'app.workers.tasks.parse_job' in names
assert 'app.workers.tasks.ping' in names
print('ok')
" && cd ..
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add server/app/workers/tasks.py
git commit -m "feat(parsing): add parse_job Celery task with state transitions"
```

---

## Task 12: CLI `parse-file` and `enqueue-parse` commands

**Files:**
- Modify: `server/cli.py`

- [ ] **Step 1: Append the new subcommands to `server/cli.py`**

After the existing `admin` Typer subapp registration, before `if __name__ == "__main__":`, add:

```python
parsing = typer.Typer(no_args_is_help=True, help="Document parsing commands")
app.add_typer(parsing, name="parsing")


@parsing.command("parse-file")
def parse_file(
    path: str = typer.Argument(..., help="Path to a local PDF or image"),
    out: str = typer.Option(None, "--out", help="Optional path to write the parsed JSON"),
) -> None:
    """Parse a local file and print a one-line summary. Bypasses Celery and the
    DB; useful for offline smoke checks and debugging.
    """
    from pathlib import Path as _Path

    from app.parsing.document_model import to_json
    from app.parsing.parser import parse

    p = _Path(path)
    if not p.exists():
        typer.echo(f"file not found: {path}", err=True)
        raise typer.Exit(code=2)

    blob_bytes = p.read_bytes()
    fmt = "pdf" if p.suffix.lower() == ".pdf" else "image"
    model, renders = parse(blob_bytes, source_format=fmt)

    typer.echo(
        f"parsed: format={model.format} pages={model.page_count} "
        f"text_spans={len(model.text_spans)} page_renders={len(renders)}"
    )
    if out:
        _Path(out).write_text(to_json(model))
        typer.echo(f"json written to: {out}")


@parsing.command("enqueue-parse")
def enqueue_parse(
    job_id: str = typer.Argument(..., help="UUID of an existing Job row"),
) -> None:
    """Submit `parse_job` to the Celery queue for an existing Job. Useful for
    re-running parsing on a job that's stuck in PENDING.
    """
    from app.workers.tasks import parse_job

    result = parse_job.delay(job_id)
    typer.echo(f"enqueued parse_job for job_id={job_id}, celery_task_id={result.id}")
```

- [ ] **Step 2: Smoke-test the new help wiring**

```bash
cd server && uv run python -m cli parsing --help && cd ..
```

Expected: typer help text listing `parse-file` and `enqueue-parse`.

- [ ] **Step 3: Commit**

```bash
git add server/cli.py
git commit -m "feat(parsing): add 'parsing parse-file' + 'parsing enqueue-parse' CLI commands"
```

---

## Task 13: Fixture-creator script + first end-to-end smoke

**Files:**
- Create: `server/fixtures/__init__.py` (empty)
- Create: `server/fixtures/build.py`

The fixture-creator script generates three small files at `server/fixtures/{sample.pdf,sample.png,sample_multipage.tiff}` from synthesized content. We don't ship binary fixtures into git for a non-test branch — the script regenerates them on demand, and the developer runs it once.

- [ ] **Step 1: Create `server/fixtures/__init__.py`**

Empty file.

- [ ] **Step 2: Write `server/fixtures/build.py`**

```python
"""Build deterministic smoke-fixture files at server/fixtures/.

Run from server/ as:

    uv run python -m fixtures.build

Outputs:
    server/fixtures/sample.pdf            (1-page PDF with native text + 1 image)
    server/fixtures/sample.png            (single-page PNG with rendered text)
    server/fixtures/sample_multipage.tiff (2-page TIFF with rendered text)
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image

OUT = Path(__file__).parent


def _img_with_text(text: str, w: int = 600, h: int = 150) -> Image.Image:
    arr = np.full((h, w, 3), 255, dtype=np.uint8)
    cv2.putText(arr, text, (20, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 0), 4)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def build_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((50, 60), "Foundation native text spans here.")
    page.insert_text((50, 120), "Below is an embedded image with OCR text:")
    img = _img_with_text("OCR ME PLEASE", w=400, h=120)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 150, 450, 270), stream=buf.getvalue())
    out = OUT / "sample.pdf"
    out.write_bytes(doc.tobytes())
    doc.close()
    print(f"wrote {out}")


def build_png() -> None:
    img = _img_with_text("PARSE THIS PNG", w=600, h=200)
    out = OUT / "sample.png"
    img.save(out, format="PNG")
    print(f"wrote {out}")


def build_tiff() -> None:
    p1 = _img_with_text("TIFF PAGE ONE")
    p2 = _img_with_text("TIFF PAGE TWO")
    out = OUT / "sample_multipage.tiff"
    p1.save(out, format="TIFF", save_all=True, append_images=[p2])
    print(f"wrote {out}")


def main() -> None:
    build_pdf()
    build_png()
    build_tiff()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Update `.gitignore` to exclude generated fixtures**

Append to root `.gitignore`:

```
# Smoke fixtures (regenerate via `uv run python -m fixtures.build`)
server/fixtures/sample.pdf
server/fixtures/sample.png
server/fixtures/sample_multipage.tiff
```

- [ ] **Step 4: Build the fixtures (locally, no Docker)**

```bash
cd server && uv run python -m fixtures.build && cd ..
```

Expected: three `wrote ...` lines. The three files appear in `server/fixtures/`.

- [ ] **Step 5: Run the CLI on the PDF fixture**

This needs Tesseract for the embedded-image OCR — run via Docker:

```bash
docker run --rm -v "$(pwd)/server:/app" -w /app redactly-server:foundation bash -c '
uv run python -m cli parsing parse-file fixtures/sample.pdf'
```

Expected: a single line like:

```
parsed: format=pdf pages=1 text_spans=N page_renders=1
```

where `N >= 7` (native text spans + OCR'd words from the embedded image; exact count depends on Tesseract's tokenization).

- [ ] **Step 6: Run the CLI on the PNG fixture**

```bash
docker run --rm -v "$(pwd)/server:/app" -w /app redactly-server:foundation bash -c '
uv run python -m cli parsing parse-file fixtures/sample.png'
```

Expected: `parsed: format=image pages=1 text_spans=N page_renders=1` with `N >= 2`.

- [ ] **Step 7: Run the CLI on the multi-page TIFF fixture**

```bash
docker run --rm -v "$(pwd)/server:/app" -w /app redactly-server:foundation bash -c '
uv run python -m cli parsing parse-file fixtures/sample_multipage.tiff'
```

Expected: `parsed: format=image pages=2 ...`.

- [ ] **Step 8: Commit**

```bash
git add server/fixtures/__init__.py server/fixtures/build.py .gitignore
git commit -m "feat(parsing): add smoke-fixture builder + .gitignore for outputs"
```

---

## Task 14: Worker end-to-end smoke (Celery picks up `parse_job`)

This task verifies the full async path: a Job row is created, the `parse_job` task is enqueued, the worker picks it up, parsing runs, the row transitions to `AWAITING_REVIEW`, and `parsed_document_uri` is populated. No code changes; just verification.

- [ ] **Step 1: Bring up the stack with the new image**

```bash
docker build -t redactly-server:foundation -f server/Dockerfile server/
docker compose up -d --build
```

Wait for services to settle. `docker compose ps` should show api/worker/beat/postgres/redis/nginx healthy.

- [ ] **Step 2: Apply the new migration (compose's api startup runs migrations automatically; verify it ran)**

```bash
docker compose exec postgres psql -U redactly -d redactly -c "
SELECT column_name FROM information_schema.columns
WHERE table_name = 'jobs' AND column_name = 'parsed_document_uri';
"
```

Expected: one row with `parsed_document_uri`.

- [ ] **Step 3: Create a Job row directly via psql** (job-creation API ships in Plan 5; for now, hand-insert)

```bash
# Bootstrap admin if not present (the bootstrap is idempotent)
docker compose exec -T api python -m cli admin create \
    --email admin@local --password "smoke-pass" --tenant_name default || true

# Get the admin user_id and tenant_id
ADMIN=$(docker compose exec -T postgres psql -U redactly -d redactly -tAc \
    "SELECT id::text, tenant_id::text FROM users WHERE email='admin@local' LIMIT 1;")
USER_ID="${ADMIN%%|*}"
TENANT_ID="${ADMIN##*|}"
echo "admin user=$USER_ID tenant=$TENANT_ID"

# Copy a fixture into the api container's blob volume
docker cp server/fixtures/sample.pdf $(docker compose ps -q api):/var/lib/redactly/blobs/sources/sample.pdf

# Insert the Job row referencing that blob
JOB_ID=$(uuidgen | tr A-Z a-z)
docker compose exec -T postgres psql -U redactly -d redactly -c "
INSERT INTO jobs (id, tenant_id, created_by, status, source_filename,
                  source_blob_uri, source_sha256, source_format, config,
                  expires_at)
VALUES ('$JOB_ID', '$TENANT_ID', '$USER_ID', 'pending', 'sample.pdf',
        'file:///var/lib/redactly/blobs/sources/sample.pdf',
        repeat('a',64), 'pdf', '{}'::jsonb,
        now() + interval '24 hours');
"
echo "job_id=$JOB_ID"
```

- [ ] **Step 4: Enqueue the parse task and watch the worker**

```bash
docker compose exec -T api python -m cli parsing enqueue-parse "$JOB_ID"

# Tail the worker until the task completes (look for "parse_job.done")
docker compose logs --since 60s --tail 200 worker | tail -30
```

Expected: structlog JSON lines `parse_job.start` and `parse_job.done` with the job_id and a `page_count`.

- [ ] **Step 5: Verify the row was updated**

```bash
docker compose exec -T postgres psql -U redactly -d redactly -c "
SELECT status, page_count, parsed_document_uri IS NOT NULL AS has_parsed
FROM jobs WHERE id = '$JOB_ID';
"
```

Expected: one row, `status=awaiting_review`, `page_count=1`, `has_parsed=t`.

- [ ] **Step 6: Verify the parsed JSON was written to blob storage**

```bash
docker compose exec -T api ls -la /var/lib/redactly/blobs/parsed/$JOB_ID/
docker compose exec -T api ls -la /var/lib/redactly/blobs/renders/$JOB_ID/
```

Expected: `document.json` exists under `parsed/`; one or more `page-N.png` files under `renders/`.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```

This task ends the smoke verification. If any step failed, inspect `docker compose logs worker` for the error; common issues are listed in the Troubleshooting section below.

- [ ] **Step 8: Commit any last-mile fixes**

If the smoke gate revealed a bug in earlier task code (e.g. the `_uri_to_key` parsing, or a wrong column name), commit the fix here:

```bash
git add -p
git commit -m "fix(parsing): smoke-verify adjustments"
```

If nothing needed fixing, skip this step.

---

## Task 15: Documentation updates

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/contributing.md`
- Modify: `docs/api.md` (small note that parsing is now wired)
- Modify: `CHANGELOG.md`
- Modify: `ROADMAP.md`

- [ ] **Step 1: Update `docs/architecture.md`**

Find the section that introduces the data flow (the "Data flow" section). Below the last paragraph, add:

```markdown
### Document parsing (now built — Plan 2)

PDF and raw-image inputs flow through the same `parse(blob, format)` entrypoint
in `server/app/parsing/parser.py` and produce a unified `DocumentModel`:

- **PDF:** PyMuPDF native text-layer extraction (per-word bboxes in user-space
  points) plus per-embedded-image OCR via Tesseract (per-word bboxes in pixel
  coordinates). Each page also rendered to a 200 DPI PNG for the future review
  UI.
- **Raw images** (JPG/PNG/TIFF/HEIC/WebP): single-page or multi-page TIFF
  iterated via `PIL.ImageSequence`. OCR runs on every page through the same
  preprocessing chain (deskew → CLAHE → denoise) defined in
  `parsing/ocr_preprocessing.py`.

The Celery `parse_job(job_id)` task drives the pipeline asynchronously: it
reads the source blob, runs `parse(...)`, writes the JSON-serialized
`DocumentModel` to `parsed/<job_id>/document.json` in blob storage, writes
each page render to `renders/<job_id>/page-<n>.png`, sets
`jobs.parsed_document_uri` and `jobs.page_count`, and transitions the row to
`AWAITING_REVIEW` (Plan 3 will insert detection between PARSING and AWAITING_REVIEW).

For offline debugging, `python -m cli parsing parse-file <path>` runs the same
pipeline synchronously without Celery or the database.
```

- [ ] **Step 2: Update `docs/contributing.md`**

Append a "Working with parsed documents" section near the existing recipes:

```markdown
### Working with parsed documents

Parsed `DocumentModel` JSON is the input contract that detection and redaction
will consume. To inspect the parser's output for a sample file:

```bash
cd server
uv run python -m fixtures.build              # generates 3 fixture files
docker run --rm -v "$(pwd):/app" -w /app redactly-server:foundation \
    uv run python -m cli parsing parse-file fixtures/sample.pdf --out /tmp/sample.json
docker run --rm -v "$(pwd):/app" -w /app redactly-server:foundation \
    cat /tmp/sample.json | head -200
```

The Tesseract binary lives in the Docker image, so smoke checks involving OCR
must run inside the container. The CLI command itself is pure Python and works
on the host *if* you've installed Tesseract locally.

When adding a new parsing capability (a new MIME type, a new preprocessing
step), keep the change isolated to `server/app/parsing/` and exercise it via
the CLI before plumbing it into `parse_job`.
```

- [ ] **Step 3: Update `docs/api.md`**

Find the "Endpoints coming in subsequent plans" list. Update the line that mentions `parse_job` (if present) to note that the task itself is shipped in Plan 2 even though the upload endpoint is Plan 5. If the list doesn't already mention parsing, leave api.md alone — the parser isn't a public surface yet.

- [ ] **Step 4: Update `CHANGELOG.md`**

At the top of the file, immediately above the `## [0.2.0-foundation] - 2026-05-01` heading, insert:

```markdown
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
```

- [ ] **Step 5: Update `ROADMAP.md`**

Find the **Plan 2 — Document parsing** entry and update its status from `Next` to `Current` (or `Shipped`, depending on how the existing file phrases the column). Also flip `Plan 1 — Foundation` from `Current` to `Shipped` if not already.

If those exact phrasings aren't present, just adjust the status sentence to reflect that Plan 2 is now done.

- [ ] **Step 6: Commit**

```bash
git add docs/architecture.md docs/contributing.md docs/api.md CHANGELOG.md ROADMAP.md
git commit -m "docs: cover parsing pipeline (architecture, contributing, CHANGELOG, ROADMAP)"
```

---

## Acceptance gate (recap)

Plan 2 is done when these are all true on a clean clone of `feature/parsing`:

- [x] `docker build -t redactly-server:foundation -f server/Dockerfile server/` succeeds (image now bakes Tesseract + libheif).
- [x] `docker run --rm ... redactly-server:foundation uv run python -m cli parsing parse-file fixtures/sample.pdf` prints `format=pdf pages=1 text_spans=N>=7 page_renders=1`.
- [x] Same command on `fixtures/sample.png` prints `format=image pages=1`.
- [x] Same command on `fixtures/sample_multipage.tiff` prints `format=image pages=2`.
- [x] After `docker compose up -d --build`, hand-inserting a Job row and running `python -m cli parsing enqueue-parse <job_id>` causes the worker to log `parse_job.done`, the row transitions to `awaiting_review`, `parsed_document_uri` is set, and `parsed/<job_id>/document.json` + `renders/<job_id>/page-N.png` files exist in the blob volume.

Once green, hand off to **Plan 3 (Detection engine)**.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pytesseract.TesseractNotFoundError` | Running on a host without Tesseract | Run via Docker — the image bakes Tesseract |
| `pillow_heif` import fails on Windows host | `libheif` not installed locally | Run via Docker; pillow-heif works fine inside the Linux container |
| `parse_job.illegal_transition` for `pending → parsing` | Job already in another state (e.g. `failed`) | Reset via SQL: `UPDATE jobs SET status='pending', error=NULL WHERE id='...'` |
| `cannot derive key from uri` in `_uri_to_key` | Job's `source_blob_uri` is an `http://` or `s3://` URL | Local backend stores `file://` URIs; for S3/MinIO support, extend `_uri_to_key` in a follow-up |
| Worker silently does nothing | `parse_job` not yet imported by the worker process | Restart worker: `docker compose restart worker` |
| Migration fails because column already exists | Foundation schema was applied via a different path | Drop the column manually, then re-run `alembic upgrade head` |
