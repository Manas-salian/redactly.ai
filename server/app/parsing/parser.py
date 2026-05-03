from typing import Literal
from app.parsing.document_model import DocumentModel
from app.parsing.image_parser import parse_image
from app.parsing.pdf_parser import parse_pdf

SourceFormat = Literal["pdf", "image"]


def parse(blob_bytes, source_format, mime=None) -> tuple[DocumentModel, dict[int, bytes]]:
    if source_format == "pdf":
        return parse_pdf(blob_bytes)
    if source_format == "image":
        return parse_image(blob_bytes, mime=mime)
    raise ValueError(f"unsupported source_format: {source_format!r}")
