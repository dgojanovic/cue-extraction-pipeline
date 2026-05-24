"""Shared extraction pipeline used by the CLI and evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from invoice_extractor.extraction.candidates import extract_candidates
from invoice_extractor.extraction.llm import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    ReasoningEffort,
    extract_invoice_from_pdf,
)
from invoice_extractor.core.models import ExtractionError, ValidatedInvoiceExtraction
from invoice_extractor.extraction.pdf_text import extract_pdf_text
from invoice_extractor.extraction.validate import validate_extraction

DEFAULT_USE_OCR = True
DEFAULT_OCR_LANG = "eng+dan+deu"
DEFAULT_TRACE_PATH = Path("outputs/traces.jsonl")


@dataclass(frozen=True)
class PipelineConfig:
    """Runtime options for one invoice extraction pipeline pass."""

    model: str = DEFAULT_MODEL
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    use_ocr: bool = DEFAULT_USE_OCR
    tesseract_cmd: str = "tesseract"
    ocr_lang: str = DEFAULT_OCR_LANG
    trace_path: Path | None = DEFAULT_TRACE_PATH


class ExtractionPipelineError(Exception):
    """Raised when one document fails without taking down the whole batch."""

    def __init__(self, document_name: str, reason: str, attempted_steps: list[str]) -> None:
        super().__init__(reason)
        self.document_name = document_name
        self.reason = reason
        self.attempted_steps = attempted_steps


def run_extraction_pipeline(
    pdf_path: str | Path,
    *,
    config: PipelineConfig | None = None,
) -> ValidatedInvoiceExtraction:
    """Run LLM extraction, local candidates, and validation for one PDF."""

    path = Path(pdf_path)
    active_config = config or PipelineConfig()
    attempted_steps: list[str] = []

    try:
        attempted_steps.append("openai_pdf_extraction")
        extraction = extract_invoice_from_pdf(
            path,
            model=active_config.model,
            reasoning_effort=active_config.reasoning_effort,
            trace_path=active_config.trace_path,
        )

        attempted_steps.append("pymupdf_text_extraction")
        if active_config.use_ocr:
            attempted_steps.append("optional_ocr")
        text_extraction = extract_pdf_text(
            path,
            use_ocr=active_config.use_ocr,
            tesseract_cmd=active_config.tesseract_cmd,
            ocr_lang=active_config.ocr_lang,
        )

        attempted_steps.append("regex_candidate_extraction")
        candidates = extract_candidates(
            text_extraction.text,
            document_name=path.name,
        )

        attempted_steps.append("candidate_validation")
        return validate_extraction(
            extraction,
            candidates,
            source_warnings=text_extraction.warnings,
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, ExtractionPipelineError):
            raise
        raise ExtractionPipelineError(path.name, str(exc), attempted_steps) from exc


def build_extraction_record(
    pdf_path: str | Path,
    *,
    config: PipelineConfig | None = None,
) -> dict:
    """Return a JSONL-ready success or error record for one PDF."""

    try:
        validated = run_extraction_pipeline(pdf_path, config=config)
        return {
            "record_type": "validated_extraction",
            **validated.model_dump(mode="json"),
        }
    except ExtractionPipelineError as exc:
        error = ExtractionError(
            document_name=exc.document_name,
            reason=exc.reason,
            attempted_steps=exc.attempted_steps,
        )
        return {"record_type": "error", **error.model_dump(mode="json")}
