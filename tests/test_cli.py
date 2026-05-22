import json
from pathlib import Path

from invoice_extractor.cli import build_parser, main


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
