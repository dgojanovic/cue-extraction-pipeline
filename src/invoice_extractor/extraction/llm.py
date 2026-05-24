"""OpenAI-backed direct-PDF invoice extraction."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI

from invoice_extractor.core.models import InvoiceExtraction
from invoice_extractor.core.tracing import append_trace, utc_now_iso

ReasoningEffort = Literal["minimal", "none", "low", "medium", "high", "xhigh"]

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_REASONING_EFFORT: ReasoningEffort = "minimal"

_EXTRACTION_PROMPT = """
Extract structured invoice data from the attached PDF.

Rules:
- Use only information visible in the PDF.
- Return unknown fields with status "unknown" and null raw_value/normalized_value.
- Do not use outside knowledge and do not invent missing values.
- For normalized_value:
  - dates must be YYYY-MM-DD when known
  - currency must be DKK, EUR, or USD when known
  - monetary amounts must be plain decimal strings with two decimals, for example 12450.00
  - tax percentage must be a plain decimal string without %, for example 25.00
- Include evidence as a short exact snippet from the invoice for every found field.
- Extract line items when visible. Each line item should include name, quantity, amount, and currency when visible.
- Distinguish invoice totals: pre-tax amount, tax percentage, tax amount, discount, and total amount.
""".strip()


def extract_invoice_from_pdf(
    pdf_path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
    client: OpenAI | None = None,
    trace_path: str | Path | None = None,
) -> InvoiceExtraction:
    """Send the original PDF directly to OpenAI and parse the structured extraction."""

    path = Path(pdf_path)
    active_client = client or _build_client()
    resolved_reasoning_effort = _resolve_reasoning_effort(model, reasoning_effort)
    pdf_bytes = path.read_bytes()
    trace_id = str(uuid.uuid4())
    started_at = utc_now_iso()
    start_time = time.perf_counter()
    trace_base = {
        "trace_id": trace_id,
        "record_type": "trace",
        "event_type": "llm_call",
        "step": "openai_pdf_extraction",
        "document_name": path.name,
        "provider": "openai",
        "model": model,
        "reasoning_effort": resolved_reasoning_effort,
        "started_at": started_at,
        "input_summary": {
            "file_name": path.name,
            "file_type": "application/pdf",
            "file_bytes": len(pdf_bytes),
            "file_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "prompt_chars": len(_EXTRACTION_PROMPT),
        },
    }

    try:
        response = active_client.responses.parse(
            model=model,
            reasoning={"effort": resolved_reasoning_effort},
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": path.name,
                            "file_data": _encode_pdf_bytes(pdf_bytes),
                        },
                        {
                            "type": "input_text",
                            "text": _EXTRACTION_PROMPT,
                        },
                    ],
                }
            ],
            text_format=InvoiceExtraction,
        )

        extraction = response.output_parsed
        extraction.document_name = path.name
        extraction.extraction_method = f"openai_pdf:{model}:reasoning_{resolved_reasoning_effort}"
        append_trace(
            trace_path,
            {
                **trace_base,
                "ended_at": utc_now_iso(),
                "latency_ms": _elapsed_ms(start_time),
                "status": "success",
                "usage": _usage_summary(response),
                "output_summary": _extraction_summary(extraction),
            },
        )
        return extraction
    except Exception as exc:
        append_trace(
            trace_path,
            {
                **trace_base,
                "ended_at": utc_now_iso(),
                "latency_ms": _elapsed_ms(start_time),
                "status": "error",
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:300],
                },
            },
        )
        raise


def _build_client() -> OpenAI:
    load_dotenv()
    return OpenAI()


def _encode_pdf_bytes(pdf_bytes: bytes) -> str:
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    return f"data:application/pdf;base64,{encoded}"


def _resolve_reasoning_effort(model: str, reasoning_effort: ReasoningEffort) -> ReasoningEffort:
    if model.startswith("gpt-5-mini") and reasoning_effort == "none":
        return "minimal"
    return reasoning_effort


def _elapsed_ms(start_time: float) -> int:
    return round((time.perf_counter() - start_time) * 1000)


def _usage_summary(response) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    if isinstance(usage, dict):
        raw_usage = usage
    elif hasattr(usage, "model_dump"):
        raw_usage = usage.model_dump(mode="json")
    else:
        raw_usage = {
            key: getattr(usage, key)
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if getattr(usage, key, None) is not None
        }

    return {
        key: value
        for key, value in raw_usage.items()
        if key.endswith("tokens") and isinstance(value, int)
    }


def _extraction_summary(extraction: InvoiceExtraction) -> dict:
    fields = list(_iter_extracted_fields(extraction))
    status_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    for field in fields:
        status_counts[str(field.status)] = status_counts.get(str(field.status), 0) + 1
        confidence_counts[str(field.confidence)] = (
            confidence_counts.get(str(field.confidence), 0) + 1
        )

    return {
        "field_status_counts": status_counts,
        "confidence_counts": confidence_counts,
        "line_item_count": len(extraction.line_items),
        "warning_count": len(extraction.warnings),
    }


def _iter_extracted_fields(extraction: InvoiceExtraction):
    yield extraction.invoice_id
    yield extraction.supplier_name
    yield extraction.currency
    yield extraction.invoice_date
    yield extraction.due_date
    yield extraction.po_reference
    yield extraction.totals.pre_tax_amount
    yield extraction.totals.tax_percentage
    yield extraction.totals.tax_amount
    yield extraction.totals.discount
    yield extraction.totals.total_amount
    for item in extraction.line_items:
        yield item.name
        yield item.quantity
        yield item.amount
        yield item.currency
