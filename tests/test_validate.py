from invoice_extractor.candidates import extract_candidates
from invoice_extractor.models import FieldStatus, InvoiceExtraction
from invoice_extractor.validate import validate_extraction


def test_validate_extraction_splits_valid_invalid_and_unchecked_fields() -> None:
    candidates = extract_candidates(
        """
        NORDIC STEEL A/S
        Fakturanummer:
        NS-2026-0431
        Fakturadato:
        2026-04-02
        Total at betale
        12.450,00 DKK
        """
    )
    extraction = InvoiceExtraction(
        document_name="invoice_01.pdf",
        invoice_id={
            "raw_value": "NS-2026-0431",
            "normalized_value": "NS-2026-0431",
            "status": FieldStatus.FOUND,
        },
        invoice_date={
            "raw_value": "2026-04-99",
            "normalized_value": "2026-04-99",
            "status": FieldStatus.FOUND,
        },
        supplier_name={
            "raw_value": "Nordic Steel A/S",
            "normalized_value": "Nordic Steel A/S",
            "status": FieldStatus.FOUND,
        },
        totals={
            "total_amount": {
                "raw_value": "12.450,00 DKK",
                "normalized_value": "12450.00",
                "status": FieldStatus.FOUND,
            }
        },
    )

    validated = validate_extraction(extraction, candidates)

    valid_paths = {field.field_path for field in validated.valid_fields}
    invalid_paths = {field.field_path for field in validated.invalid_fields}
    unchecked_paths = {field.field_path for field in validated.unchecked_fields}

    assert "invoice_id" in valid_paths
    assert "totals.total_amount" in valid_paths
    assert "invoice_date" in invalid_paths
    assert "supplier_name" in unchecked_paths
