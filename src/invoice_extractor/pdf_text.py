"""PDF text extraction helpers."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field


class PdfTextExtraction(BaseModel):
    """Text extraction result plus simple document quality signals."""

    document_name: str
    text: str
    text_source: Literal["embedded_text", "ocr", "none"]
    character_count: int = Field(ge=0)
    is_text_native: bool
    looks_scanned: bool
    warnings: list[str] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return self.text


def extract_pdf_text(
    pdf_path: str | Path,
    *,
    scanned_threshold_chars: int = 40,
    sparse_threshold_chars: int = 120,
    use_ocr: bool = False,
    tesseract_cmd: str = "tesseract",
    ocr_lang: str = "eng",
    ocr_dpi: int = 300,
    ocr_timeout_seconds: int = 30,
) -> PdfTextExtraction:
    """Extract text from a PDF and classify whether it likely needs OCR/vision."""

    path = Path(pdf_path)
    warnings = []

    if path.suffix.lower() != ".pdf":
        warnings.append("file_extension_is_not_pdf")

    document = pymupdf.open(path)
    try:
        item_count = len(document)
        text = document[0].get_text("text").strip() if item_count else ""
    finally:
        document.close()

    embedded_character_count = len(text)
    looks_scanned = embedded_character_count < scanned_threshold_chars
    is_text_native = not looks_scanned
    text_source: Literal["embedded_text", "ocr", "none"] = "embedded_text" if text else "none"

    if item_count == 0:
        warnings.append("pdf_has_no_content")
    elif embedded_character_count == 0:
        warnings.append("no_embedded_text_found")
    elif looks_scanned:
        warnings.append("embedded_text_is_too_sparse")
    elif embedded_character_count < sparse_threshold_chars:
        warnings.append("embedded_text_may_be_incomplete")

    if looks_scanned and item_count and use_ocr:
        warnings.append("ocr_attempted")
        ocr_text, ocr_warnings = _ocr_document(
            path,
            tesseract_cmd=tesseract_cmd,
            ocr_lang=ocr_lang,
            ocr_dpi=ocr_dpi,
            timeout_seconds=ocr_timeout_seconds,
        )
        warnings.extend(ocr_warnings)
        if ocr_text:
            text = ocr_text
            text_source = "ocr"
            warnings.append("ocr_text_used")

    return PdfTextExtraction(
        document_name=path.name,
        text=text,
        text_source=text_source,
        character_count=len(text),
        is_text_native=is_text_native,
        looks_scanned=looks_scanned,
        warnings=warnings,
    )


def _ocr_document(
    pdf_path: Path,
    *,
    tesseract_cmd: str,
    ocr_lang: str,
    ocr_dpi: int,
    timeout_seconds: int,
) -> tuple[str, list[str]]:
    resolved_tesseract = _resolve_tesseract_cmd(tesseract_cmd)
    if resolved_tesseract is None:
        return "", ["ocr_unavailable:tesseract_not_found"]

    with tempfile.TemporaryDirectory(prefix="invoice-ocr-") as tmpdir:
        image_path = Path(tmpdir) / "document.png"
        _render_document(pdf_path, image_path, dpi=ocr_dpi)

        result = subprocess.run(
            [resolved_tesseract, str(image_path), "stdout", "-l", ocr_lang],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )

    if result.returncode != 0:
        detail = " ".join(result.stderr.split()) or f"exit_code_{result.returncode}"
        return "", [f"ocr_failed:{detail[:300]}"]

    text = result.stdout.strip()
    if not text:
        return "", ["ocr_returned_no_text"]
    return text, []


def _resolve_tesseract_cmd(tesseract_cmd: str) -> str | None:
    command_path = Path(tesseract_cmd)
    if command_path.is_file():
        return str(command_path)
    return shutil.which(tesseract_cmd)


def _render_document(pdf_path: Path, image_path: Path, *, dpi: int) -> None:
    document = pymupdf.open(pdf_path)
    try:
        page = document[0]
        zoom = dpi / 72
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pixmap.save(image_path)
    finally:
        document.close()
