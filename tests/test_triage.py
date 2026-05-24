from decimal import Decimal

from invoice_extractor.models import InvoiceExtraction
from invoice_extractor.triage import BankTransaction, triage_extraction_record


def test_triage_auto_accepts_exact_dkk_payment() -> None:
    record = _validated_record(
        invoice_id="2026-0488",
        supplier_name="København Logistik ApS",
        currency="DKK",
        total_amount="4200.00",
        pre_tax_amount="3360.00",
        tax_amount="840.00",
    )
    transaction = BankTransaction(
        txn_id="TXN-1",
        date="2026-04-23",
        amount=Decimal("4200.00"),
        raw_amount="-4200.00",
        counterparty="KBH LOGISTIK",
        reference="Inv 2026-0488 transport",
        category="supplier_payment",
    )

    decision = triage_extraction_record(record, [transaction])

    assert decision["outcome"] == "auto_accept"
    assert decision["extraction_confidence"] == "high"
    assert decision["match_status"] == "clean"
    assert decision["reasons"] == []
    assert decision["matched_transactions"][0]["txn_id"] == "TXN-1"
    assert decision["matched_transactions"][0]["signal_checks"]["reference"]["status"] == "exact"
    assert decision["matched_transactions"][0]["signal_checks"]["amount"]["status"] == "exact"
    assert decision["matched_transactions"][0]["signal_checks"]["supplier"]["status"] == "high"


def test_triage_auto_accepts_exact_id_and_amount_with_medium_supplier() -> None:
    record = _validated_record(
        invoice_id="SVS-2026-104",
        supplier_name="Skandinavisk El & VVS",
        currency="DKK",
        total_amount="3450.00",
        pre_tax_amount="2760.00",
        tax_amount="690.00",
    )
    transaction = BankTransaction(
        txn_id="TXN-2",
        date="2026-05-02",
        amount=Decimal("3450.00"),
        raw_amount="-3450.00",
        counterparty="SKAN EL VVS A/S",
        reference="SVS-2026-104",
        category="supplier_payment",
    )

    decision = triage_extraction_record(record, [transaction])

    assert decision["outcome"] == "auto_accept"
    assert decision["match_status"] == "clean"
    assert decision["reasons"] == []
    assert decision["matched_transactions"][0]["reasons"] == []
    assert decision["matched_transactions"][0]["signal_checks"]["supplier"]["status"] == "medium"


def test_triage_auto_accept_does_not_promote_ocr_warning_to_reason() -> None:
    record = _validated_record(
        invoice_id="JT-0214",
        supplier_name="Jutland Trading A/S",
        currency="DKK",
        total_amount="7320.00",
        pre_tax_amount="5856.00",
        tax_amount="1464.00",
        source_warnings=["ocr_text_used"],
    )
    transaction = BankTransaction(
        txn_id="TXN-7",
        date="2026-05-15",
        amount=Decimal("7320.00"),
        raw_amount="-7320.00",
        counterparty="Jutland Trading",
        reference="JT-0214",
        category="supplier_payment",
    )

    decision = triage_extraction_record(record, [transaction])

    assert decision["outcome"] == "auto_accept"
    assert decision["match_status"] == "clean"
    assert decision["reasons"] == []
    assert decision["matched_transactions"][0]["reasons"] == []


def test_triage_auto_accepts_missing_id_and_amount_with_high_supplier() -> None:
    record = _validated_record(
        invoice_id="RE-2026-1198",
        supplier_name="Berliner Maschinenbau GmbH",
        currency="DKK",
        total_amount="8750.00",
        pre_tax_amount="7000.00",
        tax_amount="1750.00",
    )
    transaction = BankTransaction(
        txn_id="TXN-3",
        date="2026-05-04",
        amount=Decimal("8750.00"),
        raw_amount="-8750.00",
        counterparty="Berliner Maschinenbau",
        reference="Rechnung 2026-1198",
        category="supplier_payment",
    )

    decision = triage_extraction_record(record, [transaction])

    assert decision["outcome"] == "auto_accept"
    assert decision["match_status"] == "clean"
    assert decision["matched_transactions"][0]["signal_checks"]["reference"]["status"] == "missing"
    assert decision["matched_transactions"][0]["signal_checks"]["supplier"]["status"] == "high"


def test_triage_reviews_missing_id_and_amount_with_medium_supplier() -> None:
    record = _validated_record(
        invoice_id="RE-2026-1198",
        supplier_name="Berliner Maschinenbau GmbH",
        currency="DKK",
        total_amount="8750.00",
        pre_tax_amount="7000.00",
        tax_amount="1750.00",
    )
    transaction = BankTransaction(
        txn_id="TXN-4",
        date="2026-05-04",
        amount=Decimal("8750.00"),
        raw_amount="-8750.00",
        counterparty="BERLINER MASCH",
        reference="Rechnung 2026-1198",
        category="supplier_payment",
    )

    decision = triage_extraction_record(record, [transaction])

    assert decision["outcome"] == "review"
    assert decision["match_status"] == "questionable"
    assert "invoice_id_missing_from_bank_reference" in decision["reasons"]
    assert any(reason.startswith("supplier_fuzzy_match_medium") for reason in decision["reasons"])


def test_triage_routes_amount_mismatch_to_review() -> None:
    record = _validated_record(
        invoice_id="GOS-99421",
        supplier_name="GLOBAL OFFICE SUPPLIES LTD",
        currency="DKK",
        total_amount="1875.40",
        pre_tax_amount="1500.32",
        tax_amount="375.08",
    )
    transaction = BankTransaction(
        txn_id="TXN-2",
        date="2026-04-27",
        amount=Decimal("1837.89"),
        raw_amount="-1837.89",
        counterparty="GLOBAL OFFICE SUPP",
        reference="GOS-99421 early payment -2%",
        category="supplier_payment",
    )

    decision = triage_extraction_record(record, [transaction])

    assert decision["outcome"] == "review"
    assert decision["match_status"] == "questionable"
    assert "amount_mismatch_requires_review" in decision["reasons"]
    assert decision["matched_transactions"][0]["signal_checks"]["amount"]["status"] == "questionable"


def test_triage_rejects_extraction_error_record() -> None:
    decision = triage_extraction_record(
        {
            "record_type": "error",
            "document_name": "broken.pdf",
            "reason": "model failed",
            "attempted_steps": ["openai_pdf_extraction"],
        },
        [],
    )

    assert decision["outcome"] == "reject"
    assert decision["reasons"] == ["extraction_failed"]
    assert decision["extraction_error"]["reason"] == "model failed"


def _validated_record(
    *,
    invoice_id: str,
    supplier_name: str,
    currency: str,
    total_amount: str,
    pre_tax_amount: str,
    tax_amount: str,
    source_warnings: list[str] | None = None,
) -> dict:
    extraction = InvoiceExtraction(
        document_name="invoice.pdf",
        invoice_id={"normalized_value": invoice_id, "status": "found", "confidence": "high"},
        supplier_name={
            "normalized_value": supplier_name,
            "status": "found",
            "confidence": "high",
        },
        currency={"normalized_value": currency, "status": "found", "confidence": "high"},
        totals={
            "pre_tax_amount": {
                "normalized_value": pre_tax_amount,
                "status": "found",
                "confidence": "high",
            },
            "tax_amount": {
                "normalized_value": tax_amount,
                "status": "found",
                "confidence": "high",
            },
            "discount": {"status": "unknown"},
            "total_amount": {
                "normalized_value": total_amount,
                "status": "found",
                "confidence": "high",
            },
        },
    )
    return {
        "record_type": "validated_extraction",
        "document_name": "invoice.pdf",
        "extraction": extraction.model_dump(mode="json"),
        "valid_fields": [
            {"field_path": "invoice_id", "reason": "normalized_value_found_in_candidates"},
            {"field_path": "currency", "reason": "normalized_value_found_in_candidates"},
            {
                "field_path": "totals.total_amount",
                "reason": "normalized_value_found_in_candidates",
            },
        ],
        "invalid_fields": [],
        "unchecked_fields": [],
        "candidate_warnings": [],
        "source_warnings": source_warnings or [],
    }
