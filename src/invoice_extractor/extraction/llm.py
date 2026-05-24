"""OpenAI-backed direct-PDF invoice extraction."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI

from invoice_extractor.core.models import InvoiceExtraction

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
) -> InvoiceExtraction:
    """Send the original PDF directly to OpenAI and parse the structured extraction."""

    path = Path(pdf_path)
    active_client = client or _build_client()
    resolved_reasoning_effort = _resolve_reasoning_effort(model, reasoning_effort)
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
                        "file_data": _encode_pdf(path),
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
    return extraction


def _build_client() -> OpenAI:
    load_dotenv()
    return OpenAI()


def _encode_pdf(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:application/pdf;base64,{encoded}"


def _resolve_reasoning_effort(model: str, reasoning_effort: ReasoningEffort) -> ReasoningEffort:
    if model.startswith("gpt-5-mini") and reasoning_effort == "none":
        return "minimal"
    return reasoning_effort
