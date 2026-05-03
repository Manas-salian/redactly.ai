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
