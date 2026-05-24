from evals.evaluator import (
    GoldenRecord,
    build_report,
    score_document,
    score_pipeline_record,
)
from invoice_extractor.models import InvoiceExtraction


def test_score_document_normalizes_values_and_accepts_fuzzy_supplier_name() -> None:
    golden = GoldenRecord(
        document_name="invoice.pdf",
        expected={
            "invoice_id": "INV-100",
            "supplier_name": "Nordic Steel A/S",
            "currency": "DKK",
            "invoice_date": "2026-04-02",
            "due_date": None,
            "po_reference": "PO-123",
            "totals": {
                "pre_tax_amount": "1000.00",
                "tax_percentage": "25.00",
                "tax_amount": "250.00",
                "discount": "0.00",
                "total_amount": "1250.00",
            },
            "line_items": [
                {
                    "name": "Steel beams",
                    "quantity": "2",
                    "amount": "1000.00",
                    "currency": "DKK",
                }
            ],
        },
    )
    extraction = InvoiceExtraction(
        document_name="invoice.pdf",
        invoice_id={"normalized_value": "INV-100", "status": "found", "confidence": "high"},
        supplier_name={
            "normalized_value": "Nordic Steel AS",
            "status": "found",
            "confidence": "high",
        },
        currency={"normalized_value": "kr.", "status": "found", "confidence": "high"},
        invoice_date={"normalized_value": "02/04/2026", "status": "found", "confidence": "high"},
        due_date={"status": "unknown"},
        po_reference={"normalized_value": "po-123", "status": "found", "confidence": "high"},
        totals={
            "pre_tax_amount": {
                "normalized_value": "1.000,00",
                "status": "found",
                "confidence": "high",
            },
            "tax_percentage": {
                "normalized_value": "25%",
                "status": "found",
                "confidence": "high",
            },
            "tax_amount": {
                "normalized_value": "250.0",
                "status": "found",
                "confidence": "high",
            },
            "discount": {"normalized_value": "0", "status": "found", "confidence": "high"},
            "total_amount": {
                "normalized_value": "1250",
                "status": "found",
                "confidence": "high",
            },
        },
        line_items=[
            {
                "name": {
                    "normalized_value": "Steel beam",
                    "status": "found",
                    "confidence": "high",
                },
                "quantity": {"normalized_value": "2.0", "status": "found", "confidence": "high"},
                "amount": {
                    "normalized_value": "1,000.00",
                    "status": "found",
                    "confidence": "high",
                },
                "currency": {"normalized_value": "DKK", "status": "found", "confidence": "high"},
            }
        ],
    )

    report = build_report([score_document(golden, extraction)])

    assert report["summary"]["hallucinations"] == 0
    assert report["summary"]["misses"] == 0
    assert report["summary"]["accuracy"] == 1.0


def test_score_document_distinguishes_miss_from_hallucination() -> None:
    golden = GoldenRecord(
        document_name="invoice.pdf",
        expected={
            "invoice_id": "INV-100",
            "supplier_name": "Northwind",
            "currency": "DKK",
            "invoice_date": "2026-04-02",
            "due_date": None,
            "po_reference": None,
            "totals": {
                "pre_tax_amount": "1000.00",
                "tax_percentage": "25.00",
                "tax_amount": "250.00",
                "discount": "0.00",
                "total_amount": "1250.00",
            },
            "line_items": [],
        },
    )
    extraction = InvoiceExtraction(
        document_name="invoice.pdf",
        invoice_id={"status": "unknown"},
        supplier_name={
            "normalized_value": "Northwind",
            "status": "found",
            "confidence": "high",
        },
        currency={"normalized_value": "DKK", "status": "found", "confidence": "high"},
        invoice_date={"normalized_value": "2026-04-02", "status": "found", "confidence": "high"},
        due_date={"normalized_value": "2026-05-02", "status": "found", "confidence": "high"},
        po_reference={"normalized_value": "PO-999", "status": "found", "confidence": "high"},
        totals={
            "pre_tax_amount": {
                "normalized_value": "1000.00",
                "status": "found",
                "confidence": "high",
            },
            "tax_percentage": {
                "normalized_value": "25.00",
                "status": "found",
                "confidence": "high",
            },
            "tax_amount": {
                "normalized_value": "250.00",
                "status": "found",
                "confidence": "high",
            },
            "discount": {"normalized_value": "0.00", "status": "found", "confidence": "high"},
            "total_amount": {
                "normalized_value": "1250.00",
                "status": "found",
                "confidence": "high",
            },
        },
    )

    document_result = score_document(golden, extraction)
    outcomes_by_path = {
        result["field_path"]: result["outcome"] for result in document_result["field_results"]
    }

    assert outcomes_by_path["invoice_id"] == "miss"
    assert outcomes_by_path["due_date"] == "hallucination"
    assert outcomes_by_path["po_reference"] == "hallucination"


def test_score_document_treats_missing_numeric_fields_as_zero() -> None:
    golden = GoldenRecord(
        document_name="invoice.pdf",
        expected={
            "invoice_id": None,
            "supplier_name": None,
            "currency": None,
            "invoice_date": None,
            "due_date": None,
            "po_reference": None,
            "totals": {
                "pre_tax_amount": None,
                "tax_percentage": "0",
                "tax_amount": None,
                "discount": None,
                "total_amount": "0.00",
            },
            "line_items": [
                {
                    "name": "Zero charge",
                    "quantity": None,
                    "amount": None,
                    "currency": None,
                }
            ],
        },
    )
    extraction = InvoiceExtraction(
        document_name="invoice.pdf",
        totals={
            "pre_tax_amount": {"normalized_value": "0.00", "status": "found"},
            "tax_percentage": {"status": "unknown"},
            "tax_amount": {"normalized_value": "0", "status": "found"},
            "discount": {"status": "unknown"},
            "total_amount": {"status": "unknown"},
        },
        line_items=[
            {
                "name": {"normalized_value": "Zero charge", "status": "found"},
                "quantity": {"status": "unknown"},
                "amount": {"normalized_value": "0.00", "status": "found"},
                "currency": {"status": "unknown"},
            }
        ],
    )

    report = build_report([score_document(golden, extraction)])

    assert report["field_metrics"]["totals.pre_tax_amount"]["accuracy"] == 1.0
    assert report["field_metrics"]["totals.tax_percentage"]["accuracy"] == 1.0
    assert report["field_metrics"]["totals.tax_amount"]["accuracy"] == 1.0
    assert report["field_metrics"]["totals.discount"]["accuracy"] == 1.0
    assert report["field_metrics"]["totals.total_amount"]["accuracy"] == 1.0
    assert report["field_metrics"]["line_items[].quantity"]["accuracy"] == 1.0
    assert report["field_metrics"]["line_items[].amount"]["accuracy"] == 1.0
    assert report["summary"]["hallucinations"] == 0
    assert report["summary"]["misses"] == 0


def test_score_pipeline_record_scores_error_as_misses() -> None:
    golden = GoldenRecord(
        document_name="invoice.pdf",
        expected={
            "invoice_id": "INV-100",
            "supplier_name": None,
            "currency": "DKK",
            "invoice_date": None,
            "due_date": None,
            "po_reference": None,
            "totals": {
                "pre_tax_amount": None,
                "tax_percentage": None,
                "tax_amount": None,
                "discount": None,
                "total_amount": "1250.00",
            },
            "line_items": [],
        },
    )
    record = {
        "record_type": "error",
        "document_name": "invoice.pdf",
        "reason": "boom",
        "attempted_steps": ["openai_pdf_extraction"],
    }

    report = build_report([score_pipeline_record(golden, record)])

    assert report["summary"]["extraction_errors"] == 1
    assert report["field_metrics"]["invoice_id"]["misses"] == 1
    assert report["field_metrics"]["totals.total_amount"]["misses"] == 1
