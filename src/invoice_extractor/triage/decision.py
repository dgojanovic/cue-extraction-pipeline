"""Confidence-based invoice/payment triage for Task 3."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from invoice_extractor.core.models import FieldStatus, InvoiceExtraction, ValidatedInvoiceExtraction
from invoice_extractor.core.normalize import normalize_amount, normalize_currency
from invoice_extractor.triage.matcher import (
    BankTransaction,
    InvoiceMatchInput,
    has_multiple_close_matches,
    load_bank_transactions,
    public_match,
    score_bank_transactions,
)

VALIDATED_CRITICAL_FIELD_PATHS = frozenset(
    {
        "invoice_id",
        "currency",
        "totals.total_amount",
    }
)


@dataclass(frozen=True)
class ExtractedInvoice:
    document_name: str
    invoice_id: str | None
    supplier_name: str | None
    currency: str | None
    total_amount: Decimal | None
    extraction: InvoiceExtraction
    valid_paths: set[str]
    invalid_paths: set[str]
    source_warnings: list[str]


def run_triage(
    *,
    extractions_path: str | Path,
    bank_path: str | Path,
) -> list[dict[str, Any]]:
    """Run payment triage for extraction JSONL records against bank transactions."""

    bank_transactions = load_bank_transactions(bank_path)
    records = load_extraction_records(extractions_path)
    return [
        triage_extraction_record(
            record,
            bank_transactions,
        )
        for record in records
    ]


def load_extraction_records(path: str | Path) -> list[dict[str, Any]]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def triage_extraction_record(
    record: dict[str, Any],
    bank_transactions: list[BankTransaction],
) -> dict[str, Any]:
    record_type = record.get("record_type")
    if record_type == "error":
        return {
            "record_type": "triage_decision",
            "document_name": record.get("document_name"),
            "invoice_id": None,
            "supplier_name": None,
            "currency": None,
            "invoice_amount": None,
            "outcome": "reject",
            "extraction_confidence": "low",
            "match_status": "none",
            "reasons": ["extraction_failed"],
            "matched_transactions": [],
            "extraction_error": {
                "reason": record.get("reason"),
                "attempted_steps": record.get("attempted_steps", []),
            },
        }

    if record_type != "validated_extraction":
        return {
            "record_type": "triage_decision",
            "document_name": record.get("document_name"),
            "invoice_id": None,
            "supplier_name": None,
            "currency": None,
            "invoice_amount": None,
            "outcome": "reject",
            "extraction_confidence": "low",
            "match_status": "none",
            "reasons": [f"unsupported_extraction_record_type:{record_type}"],
            "matched_transactions": [],
        }

    validated = ValidatedInvoiceExtraction.model_validate(record)
    invoice = _build_invoice(validated)
    extraction_reasons = _extraction_reasons(invoice)
    extraction_confidence = _extraction_confidence(invoice, extraction_reasons)

    rejection_reasons = [
        reason
        for reason in extraction_reasons
        if reason.startswith("missing_")
        or reason.startswith("critical_field_failed_validation")
        or reason.startswith("total_integrity_failed")
        or reason.startswith("unsupported_currency")
    ]

    scored_transactions = score_bank_transactions(
        InvoiceMatchInput(
            invoice_id=invoice.invoice_id,
            supplier_name=invoice.supplier_name,
            total_amount=invoice.total_amount,
        ),
        bank_transactions,
    )
    plausible_matches = [match for match in scored_transactions if match["is_plausible"]]
    top_match = plausible_matches[0] if plausible_matches else None
    match_status = top_match["match_status"] if top_match else "none"
    routing_reasons = _questionable_extraction_reasons(extraction_reasons)

    if rejection_reasons:
        outcome = "reject"
        routing_reasons.extend(rejection_reasons)
    elif top_match is None:
        outcome = "review"
        routing_reasons.append("no_plausible_bank_match")
    else:
        routing_reasons.extend(top_match["reasons"])
        if has_multiple_close_matches(plausible_matches):
            routing_reasons.append("multiple_candidate_matches")

        if extraction_confidence != "high":
            outcome = "review"
            routing_reasons.append("extraction_confidence_below_auto_accept_threshold")
        elif match_status != "clean":
            outcome = "review"
            routing_reasons.append(f"match_status_{match_status}")
        else:
            outcome = "auto_accept"
            routing_reasons = _questionable_extraction_reasons(extraction_reasons)

    return {
        "record_type": "triage_decision",
        "document_name": invoice.document_name,
        "invoice_id": invoice.invoice_id,
        "supplier_name": invoice.supplier_name,
        "currency": invoice.currency,
        "invoice_amount": _format_decimal(invoice.total_amount),
        "outcome": outcome,
        "extraction_confidence": extraction_confidence,
        "match_status": match_status,
        "reasons": _dedupe_preserving_order(routing_reasons),
        "matched_transactions": [public_match(match) for match in plausible_matches[:3]],
    }


def _build_invoice(validated: ValidatedInvoiceExtraction) -> ExtractedInvoice:
    extraction = validated.extraction
    return ExtractedInvoice(
        document_name=validated.document_name,
        invoice_id=_field_value(extraction.invoice_id),
        supplier_name=_field_value(extraction.supplier_name),
        currency=normalize_currency(_field_value(extraction.currency)),
        total_amount=_field_decimal(extraction.totals.total_amount),
        extraction=extraction,
        valid_paths={field.field_path for field in validated.valid_fields},
        invalid_paths={field.field_path for field in validated.invalid_fields},
        source_warnings=validated.source_warnings,
    )


def _extraction_reasons(invoice: ExtractedInvoice) -> list[str]:
    reasons: list[str] = []
    if invoice.invoice_id is None:
        reasons.append("missing_invoice_id")
    if invoice.supplier_name is None:
        reasons.append("missing_supplier_name")
    if invoice.currency is None:
        reasons.append("missing_currency")
    if invoice.total_amount is None:
        reasons.append("missing_total_amount")
    if invoice.currency is not None and invoice.currency not in {"DKK", "EUR", "USD"}:
        reasons.append("unsupported_currency")

    invalid_critical_paths = sorted(invoice.invalid_paths & VALIDATED_CRITICAL_FIELD_PATHS)
    if invalid_critical_paths:
        reasons.append(f"critical_field_failed_validation:{','.join(invalid_critical_paths)}")

    missing_validations = sorted(VALIDATED_CRITICAL_FIELD_PATHS - invoice.valid_paths)
    if not invalid_critical_paths and not any(reason.startswith("missing_") for reason in reasons):
        if missing_validations:
            reasons.append(f"critical_candidate_validation_missing:{','.join(missing_validations)}")
        else:
            reasons.append("critical_candidate_validation_passed")

    if _total_integrity_failed(invoice.extraction):
        reasons.append("total_integrity_failed")
    else:
        reasons.append("total_integrity_check_passed")

    return reasons


def _extraction_confidence(invoice: ExtractedInvoice, reasons: list[str]) -> str:
    if any(
        reason.startswith("missing_")
        or reason.startswith("critical_field_failed_validation")
        or reason == "total_integrity_failed"
        for reason in reasons
    ):
        return "low"

    critical_fields = (
        invoice.extraction.invoice_id,
        invoice.extraction.supplier_name,
        invoice.extraction.currency,
        invoice.extraction.totals.total_amount,
    )
    if any(str(field.confidence) != "high" for field in critical_fields):
        return "medium"
    if any(reason.startswith("critical_candidate_validation_missing") for reason in reasons):
        return "medium"
    return "high"


def _questionable_extraction_reasons(reasons: list[str]) -> list[str]:
    return [
        reason
        for reason in reasons
        if reason.startswith("missing_")
        or reason.startswith("critical_field_failed_validation")
        or reason.startswith("critical_candidate_validation_missing")
        or reason == "total_integrity_failed"
        or reason == "unsupported_currency"
    ]


def _total_integrity_failed(extraction: InvoiceExtraction) -> bool:
    pre_tax = _field_decimal(extraction.totals.pre_tax_amount)
    tax = _field_decimal(extraction.totals.tax_amount)
    discount = _field_decimal(extraction.totals.discount) or Decimal("0.00")
    total = _field_decimal(extraction.totals.total_amount)
    if pre_tax is None or tax is None or total is None:
        return False
    expected_total = pre_tax + tax - discount
    return abs(expected_total - total) > Decimal("0.02")


def _field_value(field) -> str | None:
    if field.status == FieldStatus.UNKNOWN:
        return None
    return field.normalized_value or field.raw_value


def _field_decimal(field) -> Decimal | None:
    return _parse_decimal(_field_value(field))


def _parse_decimal(value: str | None) -> Decimal | None:
    normalized = normalize_amount(value)
    if normalized is None:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, ".2f")


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped
