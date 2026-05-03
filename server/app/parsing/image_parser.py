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
