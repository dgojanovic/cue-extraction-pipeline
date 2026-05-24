from invoice_extractor.core.models import ConfidenceLevel, FieldStatus, InvoiceExtraction, LineItem


def test_invoice_extraction_defaults_optional_fields_to_unknown() -> None:
    extraction = InvoiceExtraction(document_name="invoice_01.pdf")

    assert extraction.invoice_id.status is FieldStatus.UNKNOWN
    assert extraction.totals.total_amount.confidence is ConfidenceLevel.UNKNOWN
    assert extraction.line_items == []


def test_line_item_contains_required_extraction_fields() -> None:
    item = LineItem(
        name={"raw_value": "Consulting", "normalized_value": "Consulting", "status": "found"},
        quantity={"raw_value": "2", "normalized_value": "2", "status": "found"},
        amount={"raw_value": "DKK 500.00", "normalized_value": "500.00", "status": "found"},
        currency={"raw_value": "DKK", "normalized_value": "DKK", "status": "found"},
    )

    assert item.name.normalized_value == "Consulting"
    assert item.quantity.normalized_value == "2"
    assert item.amount.normalized_value == "500.00"
    assert item.currency.normalized_value == "DKK"
