"""Candidate-based validation for LLM invoice extraction results."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from invoice_extractor.extraction.candidates import ExtractionCandidates
from invoice_extractor.core.models import (
    ExtractedField,
    FieldStatus,
    FieldValidation,
    InvoiceExtraction,
    ValidatedInvoiceExtraction,
)
from invoice_extractor.core.normalize import (
    normalize_amount,
    normalize_currency,
    normalize_date,
    normalize_percentage,
)

Normalizer = Callable[[str | None], str | None]


def validate_extraction(
    extraction: InvoiceExtraction,
    candidates: ExtractionCandidates,
    *,
    source_warnings: list[str] | None = None,
) -> ValidatedInvoiceExtraction:
    """Compare LLM-selected fields against independent local candidates."""

    valid_fields: list[FieldValidation] = []
    invalid_fields: list[FieldValidation] = []
    unchecked_fields: list[FieldValidation] = []

    id_values = {candidate.normalized_value for candidate in candidates.id_candidates}
    date_values = {candidate.normalized_value.isoformat() for candidate in candidates.dates}
    currency_values = {candidate.normalized_value for candidate in candidates.currencies}
    amount_values = {_format_decimal(candidate.normalized_value) for candidate in candidates.amounts}
    percentage_values = {
        _format_decimal(candidate.normalized_value) for candidate in candidates.tax_percentages
    }

    _validate_candidate_field(
        "invoice_id",
        extraction.invoice_id,
        id_values,
        _normalize_text,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "po_reference",
        extraction.po_reference,
        id_values,
        _normalize_text,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "currency",
        extraction.currency,
        currency_values,
        normalize_currency,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "invoice_date",
        extraction.invoice_date,
        date_values,
        normalize_date,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "due_date",
        extraction.due_date,
        date_values,
        normalize_date,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )

    _validate_candidate_field(
        "totals.pre_tax_amount",
        extraction.totals.pre_tax_amount,
        amount_values,
        normalize_amount,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "totals.tax_percentage",
        extraction.totals.tax_percentage,
        percentage_values,
        normalize_percentage,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "totals.tax_amount",
        extraction.totals.tax_amount,
        amount_values,
        normalize_amount,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "totals.discount",
        extraction.totals.discount,
        amount_values,
        normalize_amount,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )
    _validate_candidate_field(
        "totals.total_amount",
        extraction.totals.total_amount,
        amount_values,
        normalize_amount,
        valid_fields,
        invalid_fields,
        unchecked_fields,
    )

    _add_unchecked_field("supplier_name", extraction.supplier_name, unchecked_fields)
    for index, item in enumerate(extraction.line_items):
        _add_unchecked_field(f"line_items[{index}].name", item.name, unchecked_fields)
        _add_unchecked_field(f"line_items[{index}].quantity", item.quantity, unchecked_fields)
        _validate_candidate_field(
            f"line_items[{index}].amount",
            item.amount,
            amount_values,
            normalize_amount,
            valid_fields,
            invalid_fields,
            unchecked_fields,
        )
        _validate_candidate_field(
            f"line_items[{index}].currency",
            item.currency,
            currency_values,
            normalize_currency,
            valid_fields,
            invalid_fields,
            unchecked_fields,
        )

    return ValidatedInvoiceExtraction(
        document_name=extraction.document_name,
        extraction=extraction,
        valid_fields=valid_fields,
        invalid_fields=invalid_fields,
        unchecked_fields=unchecked_fields,
        candidate_warnings=candidates.warnings,
        source_warnings=source_warnings or [],
    )


def _validate_candidate_field(
    field_path: str,
    field: ExtractedField,
    candidate_values: set[str],
    normalizer: Normalizer,
    valid_fields: list[FieldValidation],
    invalid_fields: list[FieldValidation],
    unchecked_fields: list[FieldValidation],
) -> None:
    value = _field_value(field)
    if field.status is FieldStatus.UNKNOWN or value is None:
        unchecked_fields.append(
            FieldValidation(field_path=field_path, value=value, reason="field_unknown")
        )
        return

    normalized = normalizer(value)
    if normalized is None:
        invalid_fields.append(
            FieldValidation(
                field_path=field_path,
                value=value,
                reason="value_failed_normalization",
            )
        )
        return

    if normalized in candidate_values:
        valid_fields.append(
            FieldValidation(
                field_path=field_path,
                value=value,
                normalized_value=normalized,
                matched_candidate=normalized,
                reason="normalized_value_found_in_candidates",
            )
        )
        return

    invalid_fields.append(
        FieldValidation(
            field_path=field_path,
            value=value,
            normalized_value=normalized,
            reason="normalized_value_not_found_in_candidates",
        )
    )


def _add_unchecked_field(
    field_path: str,
    field: ExtractedField,
    unchecked_fields: list[FieldValidation],
) -> None:
    value = _field_value(field)
    unchecked_fields.append(
        FieldValidation(
            field_path=field_path,
            value=value,
            normalized_value=field.normalized_value,
            reason="no_candidate_family",
        )
    )


def _field_value(field: ExtractedField) -> str | None:
    return field.normalized_value or field.raw_value


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def _format_decimal(value: Decimal) -> str:
    return format(value, ".2f")
