"""Command-line entry points for the invoice extraction pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from invoice_extractor import __version__
from invoice_extractor.candidates import extract_candidates
from invoice_extractor.models import ExtractionError
from invoice_extractor.pdf_text import extract_pdf_text


def build_parser() -> argparse.ArgumentParser:
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

    return parser


def _add_ocr_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Run Tesseract OCR when embedded PDF text is missing or too sparse.",
    )
    parser.add_argument(
        "--tesseract-cmd",
        default="tesseract",
        help="Tesseract executable name or path.",
    )
    parser.add_argument(
        "--ocr-lang",
        default="eng",
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


def _extract_text_from_args(pdf_path: Path, args: argparse.Namespace):
    return extract_pdf_text(
        pdf_path,
        use_ocr=args.ocr,
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


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return

    args.handler(args)
