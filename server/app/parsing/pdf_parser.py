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
