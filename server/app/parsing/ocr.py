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
