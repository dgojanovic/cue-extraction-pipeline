from pathlib import Path

from invoice_extractor.llm_extract import extract_invoice_from_pdf
from invoice_extractor.models import InvoiceExtraction


class _FakeResponses:
    def __init__(self) -> None:
        self.input_seen = None
        self.reasoning_seen = None

    def parse(self, **kwargs):
        self.input_seen = kwargs["input"]
        self.reasoning_seen = kwargs["reasoning"]
        return type(
            "FakeResponse",
            (),
            {"output_parsed": InvoiceExtraction(document_name="model-value.pdf")},
        )()


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def test_extract_invoice_from_pdf_passes_original_pdf_file_data() -> None:
    client = _FakeClient()

    extraction = extract_invoice_from_pdf(
        Path("pdf_invoices/invoice_01.pdf"),
        model="test-model",
        reasoning_effort="low",
        client=client,
    )

    content = client.responses.input_seen[0]["content"]

    assert extraction.document_name == "invoice_01.pdf"
    assert extraction.extraction_method == "openai_pdf:test-model:reasoning_low"
    assert client.responses.reasoning_seen == {"effort": "low"}
    assert content[0]["type"] == "input_file"
    assert content[0]["filename"] == "invoice_01.pdf"
    assert content[0]["file_data"].startswith("data:application/pdf;base64,")
    assert content[1]["type"] == "input_text"
