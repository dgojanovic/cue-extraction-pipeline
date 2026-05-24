from pathlib import Path
import json

from invoice_extractor.extraction.llm import extract_invoice_from_pdf
from invoice_extractor.core.models import InvoiceExtraction


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
            {
                "output_parsed": InvoiceExtraction(document_name="model-value.pdf"),
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )()


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def test_extract_invoice_from_pdf_passes_original_pdf_file_data(tmp_path) -> None:
    client = _FakeClient()
    trace_path = tmp_path / "traces.jsonl"

    extraction = extract_invoice_from_pdf(
        Path("pdf_invoices/invoice_01.pdf"),
        model="test-model",
        reasoning_effort="low",
        client=client,
        trace_path=trace_path,
    )

    content = client.responses.input_seen[0]["content"]

    assert extraction.document_name == "invoice_01.pdf"
    assert extraction.extraction_method == "openai_pdf:test-model:reasoning_low"
    assert client.responses.reasoning_seen == {"effort": "low"}
    assert content[0]["type"] == "input_file"
    assert content[0]["filename"] == "invoice_01.pdf"
    assert content[0]["file_data"].startswith("data:application/pdf;base64,")
    assert content[1]["type"] == "input_text"

    trace = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert trace["record_type"] == "trace"
    assert trace["event_type"] == "llm_call"
    assert trace["document_name"] == "invoice_01.pdf"
    assert trace["model"] == "test-model"
    assert trace["status"] == "success"
    assert trace["usage"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert trace["input_summary"]["file_bytes"] > 0
    assert "file_sha256" in trace["input_summary"]
    assert "field_status_counts" in trace["output_summary"]
