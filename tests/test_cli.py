import json
from pathlib import Path

import pymupdf

from invoice_extractor.cli import build_parser, main
from invoice_extractor.models import InvoiceExtraction


def test_cli_parser_builds() -> None:
    parser = build_parser()

    assert parser.prog == "invoice-extract"


def test_inspect_text_command_writes_jsonl_report(tmp_path) -> None:
    output_path = tmp_path / "pdf_text.jsonl"

    main(["inspect-text", "pdf_invoices", "--out", str(output_path)])

    records = [json.loads(line) for line in output_path.read_text().splitlines()]

    assert len(records) == len(list(Path("pdf_invoices").glob("*.pdf")))
    assert any(
        record["document_name"] == "invoice_07.pdf" and record["looks_scanned"]
        for record in records
    )


def test_inspect_candidates_command_writes_jsonl_report(tmp_path) -> None:
    output_path = tmp_path / "candidates.jsonl"

    main(["inspect-candidates", "pdf_invoices", "--out", str(output_path)])

    records = [json.loads(line) for line in output_path.read_text().splitlines()]

    assert len(records) == len(list(Path("pdf_invoices").glob("*.pdf")))
    assert any(
        record["document_name"] == "invoice_01.pdf"
        and record["id_candidates"][0]["normalized_value"] == "NS-2026-0431"
        for record in records
    )
    assert any(
        record["document_name"] == "invoice_07.pdf"
        and record["warnings"] == ["no_text_for_candidate_extraction"]
        for record in records
    )


def test_inspect_candidates_command_can_use_ocr(tmp_path) -> None:
    output_path = tmp_path / "candidates.jsonl"
    fake_tesseract = tmp_path / "tesseract"
    fake_tesseract.write_text(
        "#!/bin/sh\nprintf 'Invoice OCR-123\\nTotal DKK 100.00\\n'\n",
        encoding="utf-8",
    )
    fake_tesseract.chmod(0o755)

    main(
        [
            "inspect-candidates",
            "pdf_invoices",
            "--ocr",
            "--tesseract-cmd",
            str(fake_tesseract),
            "--out",
            str(output_path),
        ]
    )

    records = [json.loads(line) for line in output_path.read_text().splitlines()]

    assert any(
        record["document_name"] == "invoice_07.pdf"
        and record["id_candidates"][0]["normalized_value"] == "OCR-123"
        for record in records
    )


def test_extract_command_writes_validated_extraction_report(tmp_path, monkeypatch) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "invoice.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Invoice INV-100\nDate 2026-04-02\nTotal DKK 100.00")
    document.save(pdf_path)
    document.close()

    def fake_extract_invoice_from_pdf(pdf_path, *, model, reasoning_effort):
        return InvoiceExtraction(
            document_name=Path(pdf_path).name,
            extraction_method=f"fake:{model}:{reasoning_effort}",
            invoice_id={
                "raw_value": "INV-100",
                "normalized_value": "INV-100",
                "status": "found",
            },
            totals={
                "total_amount": {
                    "raw_value": "DKK 100.00",
                    "normalized_value": "100.00",
                    "status": "found",
                }
            },
        )

    monkeypatch.setattr(
        "invoice_extractor.pipeline.extract_invoice_from_pdf",
        fake_extract_invoice_from_pdf,
    )
    output_path = tmp_path / "extractions.jsonl"

    main(
        [
            "extract",
            str(pdf_dir),
            "--model",
            "test-model",
            "--reasoning-effort",
            "low",
            "--out",
            str(output_path),
        ]
    )

    records = [json.loads(line) for line in output_path.read_text().splitlines()]

    assert records[0]["record_type"] == "validated_extraction"
    assert records[0]["valid_fields"][0]["field_path"] == "invoice_id"


def test_extract_command_writes_error_record_for_failed_document(tmp_path, monkeypatch) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "broken.pdf"
    pdf_path.write_bytes(b"not a real pdf")

    def fake_extract_invoice_from_pdf(pdf_path, *, model, reasoning_effort):
        raise RuntimeError("model call failed")

    monkeypatch.setattr(
        "invoice_extractor.pipeline.extract_invoice_from_pdf",
        fake_extract_invoice_from_pdf,
    )
    output_path = tmp_path / "extractions.jsonl"

    main(
        [
            "extract",
            str(pdf_dir),
            "--model",
            "test-model",
            "--reasoning-effort",
            "low",
            "--out",
            str(output_path),
        ]
    )

    records = [json.loads(line) for line in output_path.read_text().splitlines()]

    assert records == [
        {
            "record_type": "error",
            "document_name": "broken.pdf",
            "reason": "model call failed",
            "attempted_steps": ["openai_pdf_extraction"],
        }
    ]
