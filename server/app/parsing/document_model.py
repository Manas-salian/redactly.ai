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
