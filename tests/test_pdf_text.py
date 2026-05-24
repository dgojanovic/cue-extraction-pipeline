from pathlib import Path

import pymupdf

from invoice_extractor.extraction.pdf_text import extract_pdf_text


def test_extract_pdf_text_reads_embedded_text() -> None:
    extraction = extract_pdf_text(Path("pdf_invoices/invoice_01.pdf"))

    assert extraction.document_name == "invoice_01.pdf"
    assert "NS-2026-0431" in extraction.full_text
    assert extraction.text_source == "embedded_text"
    assert extraction.character_count > 0
    assert extraction.is_text_native is True
    assert extraction.looks_scanned is False


def test_extract_pdf_text_flags_image_only_pdf_as_scanned_like() -> None:
    extraction = extract_pdf_text(Path("pdf_invoices/invoice_07.pdf"))

    assert extraction.character_count == 0
    assert extraction.text_source == "none"
    assert extraction.looks_scanned is True
    assert "no_embedded_text_found" in extraction.warnings


def test_extract_pdf_text_reports_unavailable_ocr_for_scanned_pdf() -> None:
    extraction = extract_pdf_text(
        Path("pdf_invoices/invoice_07.pdf"),
        use_ocr=True,
        tesseract_cmd="definitely-missing-tesseract",
    )

    assert extraction.character_count == 0
    assert extraction.text_source == "none"
    assert "ocr_attempted" in extraction.warnings
    assert "ocr_unavailable:tesseract_not_found" in extraction.warnings


def test_extract_pdf_text_uses_ocr_when_available(tmp_path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf_path)
    document.close()

    fake_tesseract = tmp_path / "tesseract"
    fake_tesseract.write_text(
        "#!/bin/sh\nprintf 'OCR Invoice OCR-123\\nTotal DKK 100.00\\n'\n",
        encoding="utf-8",
    )
    fake_tesseract.chmod(0o755)

    extraction = extract_pdf_text(
        pdf_path,
        use_ocr=True,
        tesseract_cmd=str(fake_tesseract),
    )

    assert extraction.text_source == "ocr"
    assert "OCR-123" in extraction.text
    assert "ocr_text_used" in extraction.warnings
