"""Command-line entry points for the invoice extraction pipeline."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from invoice_extractor import __version__
from invoice_extractor.candidates import extract_candidates
from invoice_extractor.llm_extract import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
)
from invoice_extractor.models import ExtractionError
from invoice_extractor.pdf_text import extract_pdf_text
from invoice_extractor.pipeline import (
    DEFAULT_OCR_LANG,
    DEFAULT_USE_OCR,
    PipelineConfig,
    build_extraction_record,
)
from invoice_extractor.triage import run_triage


def build_parser() -> argparse.ArgumentParser:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="invoice-extract",
        description="Run invoice extraction and reconciliation tasks.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    inspect_text_parser = subparsers.add_parser(
        "inspect-text",
        help="Extract embedded text from every PDF in a directory and write a JSONL report.",
    )
    inspect_text_parser.add_argument(
        "pdf_dir",
        type=Path,
        nargs="?",
        default=Path("pdf_invoices"),
        help="Directory containing invoice PDFs.",
    )
    inspect_text_parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/pdf_text.jsonl"),
        help="Path to write the JSONL text extraction report.",
    )
    _add_ocr_arguments(inspect_text_parser)
    inspect_text_parser.set_defaults(handler=inspect_text_command)

    inspect_candidates_parser = subparsers.add_parser(
        "inspect-candidates",
        help="Extract regex candidates from every PDF in a directory and write a JSONL report.",
    )
    inspect_candidates_parser.add_argument(
        "pdf_dir",
        type=Path,
        nargs="?",
        default=Path("pdf_invoices"),
        help="Directory containing invoice PDFs.",
    )
    inspect_candidates_parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/candidates.jsonl"),
        help="Path to write the JSONL candidate report.",
    )
    _add_ocr_arguments(inspect_candidates_parser)
    inspect_candidates_parser.set_defaults(handler=inspect_candidates_command)

    extract_parser = subparsers.add_parser(
        "extract",
        help="Run direct-PDF LLM extraction and validate extracted fields against local candidates.",
    )
    extract_parser.add_argument(
        "pdf_dir",
        type=Path,
        nargs="?",
        default=Path("pdf_invoices"),
        help="Directory containing invoice PDFs.",
    )
    extract_parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/extractions.jsonl"),
        help="Path to write validated extraction JSONL.",
    )
    extract_parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        help="OpenAI model to use for direct-PDF extraction.",
    )
    extract_parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "none", "low", "medium", "high", "xhigh"],
        default=os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
        help="Reasoning effort for GPT-5-family models.",
    )
    _add_ocr_arguments(extract_parser)
    extract_parser.set_defaults(handler=extract_command)

    triage_parser = subparsers.add_parser(
        "triage",
        help="Match validated invoice extractions against bank transactions and route outcomes.",
    )
    triage_parser.add_argument(
        "--extractions",
        type=Path,
        default=Path("outputs/extractions.jsonl"),
        help="Path to validated extraction JSONL.",
    )
    triage_parser.add_argument(
        "--bank",
        type=Path,
        default=Path("bank_transactions.csv"),
        help="Path to bank transaction CSV.",
    )
    triage_parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/triage_report.jsonl"),
        help="Path to write triage JSONL.",
    )
    triage_parser.set_defaults(handler=triage_command)

    return parser


def _add_ocr_arguments(parser: argparse.ArgumentParser) -> None:
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--ocr",
        dest="use_ocr",
        action="store_true",
        default=DEFAULT_USE_OCR,
        help="Run Tesseract OCR when embedded PDF text is missing or too sparse. Enabled by default.",
    )
    ocr_group.add_argument(
        "--no-ocr",
        dest="use_ocr",
        action="store_false",
        help="Disable Tesseract OCR fallback.",
    )
    parser.add_argument(
        "--tesseract-cmd",
        default="tesseract",
        help="Tesseract executable name or path.",
    )
    parser.add_argument(
        "--ocr-lang",
        default=DEFAULT_OCR_LANG,
        help="Tesseract language code, for example eng or eng+dan+deu.",
    )


def inspect_text_command(args: argparse.Namespace) -> None:
    pdf_paths = sorted(args.pdf_dir.glob("*.pdf"))
    records = []

    for pdf_path in pdf_paths:
        try:
            extraction = _extract_text_from_args(pdf_path, args)
            records.append({"record_type": "pdf_text", **extraction.model_dump(mode="json")})
        except Exception as exc:  # noqa: BLE001
            error = ExtractionError(
                document_name=pdf_path.name,
                reason=str(exc),
                attempted_steps=["pymupdf_text_extraction"],
            )
            records.append({"record_type": "error", **error.model_dump(mode="json")})

    _write_jsonl(args.out, records)
    _print_inspect_text_summary(records, args.out)


def inspect_candidates_command(args: argparse.Namespace) -> None:
    pdf_paths = sorted(args.pdf_dir.glob("*.pdf"))
    records = []

    for pdf_path in pdf_paths:
        try:
            text_extraction = _extract_text_from_args(pdf_path, args)
            candidates = extract_candidates(
                text_extraction.text,
                document_name=pdf_path.name,
            )
            records.append(
                {
                    "record_type": "candidates",
                    **candidates.model_dump(mode="json"),
                    "source_warnings": text_extraction.warnings,
                }
            )
        except Exception as exc:  # noqa: BLE001
            error = ExtractionError(
                document_name=pdf_path.name,
                reason=str(exc),
                attempted_steps=["pymupdf_text_extraction", "regex_candidate_extraction"],
            )
            records.append({"record_type": "error", **error.model_dump(mode="json")})

    _write_jsonl(args.out, records)
    _print_inspect_candidates_summary(records, args.out)


def extract_command(args: argparse.Namespace) -> None:
    pdf_paths = sorted(args.pdf_dir.glob("*.pdf"))
    records = []
    config = PipelineConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        use_ocr=args.use_ocr,
        tesseract_cmd=args.tesseract_cmd,
        ocr_lang=args.ocr_lang,
    )

    for pdf_path in pdf_paths:
        records.append(build_extraction_record(pdf_path, config=config))

    _write_jsonl(args.out, records)
    _print_extract_summary(records, args.out)


def triage_command(args: argparse.Namespace) -> None:
    records = run_triage(
        extractions_path=args.extractions,
        bank_path=args.bank,
    )
    _write_jsonl(args.out, records)
    _print_triage_summary(records, args.out)


def _extract_text_from_args(pdf_path: Path, args: argparse.Namespace):
    return extract_pdf_text(
        pdf_path,
        use_ocr=args.use_ocr,
        tesseract_cmd=args.tesseract_cmd,
        ocr_lang=args.ocr_lang,
    )


def _write_jsonl(output_path: Path, records: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_inspect_text_summary(records: list[dict], output_path: Path) -> None:
    pdf_records = [record for record in records if record["record_type"] == "pdf_text"]
    error_records = [record for record in records if record["record_type"] == "error"]
    scanned_records = [record for record in pdf_records if record["looks_scanned"]]
    ocr_records = [record for record in pdf_records if record["text_source"] == "ocr"]

    print(
        "Processed "
        f"{len(records)} files: "
        f"{len(pdf_records)} text reports, "
        f"{len(scanned_records)} scanned-like, "
        f"{len(ocr_records)} OCR, "
        f"{len(error_records)} errors."
    )

    for record in scanned_records + error_records:
        detail = record.get("warnings") or record.get("reason")
        print(f"- {record['document_name']}: {detail}")

    print(f"Wrote {output_path}")


def _print_inspect_candidates_summary(records: list[dict], output_path: Path) -> None:
    candidate_records = [record for record in records if record["record_type"] == "candidates"]
    error_records = [record for record in records if record["record_type"] == "error"]
    empty_records = [
        record
        for record in candidate_records
        if not record["id_candidates"] and not record["amounts"] and not record["dates"]
    ]

    print(
        "Processed "
        f"{len(records)} files: "
        f"{len(candidate_records)} candidate reports, "
        f"{len(empty_records)} empty, "
        f"{len(error_records)} errors."
    )

    for record in empty_records + error_records:
        detail = record.get("source_warnings") or record.get("warnings") or record.get("reason")
        print(f"- {record['document_name']}: {detail}")

    print(f"Wrote {output_path}")


def _print_extract_summary(records: list[dict], output_path: Path) -> None:
    extraction_records = [
        record for record in records if record["record_type"] == "validated_extraction"
    ]
    error_records = [record for record in records if record["record_type"] == "error"]
    invalid_count = sum(len(record["invalid_fields"]) for record in extraction_records)

    print(
        "Processed "
        f"{len(records)} files: "
        f"{len(extraction_records)} validated extractions, "
        f"{invalid_count} invalid fields, "
        f"{len(error_records)} errors."
    )

    for record in error_records:
        print(f"- {record['document_name']}: {record['reason']}")

    print(f"Wrote {output_path}")


def _print_triage_summary(records: list[dict], output_path: Path) -> None:
    outcome_counts = {
        "auto_accept": sum(1 for record in records if record["outcome"] == "auto_accept"),
        "review": sum(1 for record in records if record["outcome"] == "review"),
        "reject": sum(1 for record in records if record["outcome"] == "reject"),
    }
    print(
        "Triaged "
        f"{len(records)} invoices: "
        f"{outcome_counts['auto_accept']} auto-accept, "
        f"{outcome_counts['review']} review, "
        f"{outcome_counts['reject']} reject."
    )
    for record in records:
        if record["outcome"] != "auto_accept":
            print(f"- {record['document_name']}: {record['outcome']} ({', '.join(record['reasons'])})")
    print(f"Wrote {output_path}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return

    args.handler(args)


if __name__ == "__main__":
    main()
