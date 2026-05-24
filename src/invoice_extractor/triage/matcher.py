"""Bank transaction matching signals for invoice triage."""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from invoice_extractor.core.normalize import normalize_amount

SUPPLIER_HIGH_THRESHOLD = 0.75
SUPPLIER_MEDIUM_THRESHOLD = 0.50
AMOUNT_REVIEW_TOLERANCE = Decimal("1.00")


@dataclass(frozen=True)
class BankTransaction:
    txn_id: str
    date: str
    amount: Decimal
    raw_amount: str
    counterparty: str
    reference: str
    category: str


@dataclass(frozen=True)
class InvoiceMatchInput:
    invoice_id: str | None
    supplier_name: str | None
    total_amount: Decimal | None


def load_bank_transactions(path: str | Path) -> list[BankTransaction]:
    transactions: list[BankTransaction] = []
    with Path(path).open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            amount = _parse_decimal(row.get("amount"))
            if amount is None:
                continue
            transactions.append(
                BankTransaction(
                    txn_id=row.get("txn_id", ""),
                    date=row.get("date", ""),
                    amount=abs(amount),
                    raw_amount=row.get("amount", ""),
                    counterparty=row.get("counterparty", ""),
                    reference=row.get("reference", ""),
                    category=row.get("category", ""),
                )
            )
    return transactions


def score_bank_transactions(
    invoice: InvoiceMatchInput,
    bank_transactions: list[BankTransaction],
) -> list[dict[str, Any]]:
    return sorted(
        (
            _check_bank_transaction(invoice, transaction)
            for transaction in bank_transactions
        ),
        key=lambda match: match["sort_score"],
        reverse=True,
    )


def has_multiple_close_matches(matches: list[dict[str, Any]]) -> bool:
    if len(matches) < 2:
        return False
    return matches[1]["sort_score"] >= matches[0]["sort_score"] - 0.05


def public_match(match: dict[str, Any]) -> dict[str, Any]:
    public = dict(match)
    public.pop("is_plausible", None)
    public.pop("sort_score", None)
    return public


def _check_bank_transaction(
    invoice: InvoiceMatchInput,
    transaction: BankTransaction,
) -> dict[str, Any]:
    reference_status, reference_reason, reference_strength = _reference_check(
        invoice.invoice_id,
        transaction.reference,
    )
    supplier_score = _supplier_score(invoice.supplier_name, transaction.counterparty)
    supplier_status = _supplier_status(supplier_score)
    amount_status, amount_reason, amount_strength = _amount_check(
        invoice,
        transaction,
    )
    match_status = _match_status(reference_status, amount_status, supplier_status)
    sort_score = _sort_score(reference_strength, amount_strength, supplier_score)

    return {
        "txn_id": transaction.txn_id,
        "date": transaction.date,
        "amount": transaction.raw_amount,
        "counterparty": transaction.counterparty,
        "reference": transaction.reference,
        "category": transaction.category,
        "is_plausible": match_status != "none",
        "match_status": match_status,
        "sort_score": round(sort_score, 4),
        "signal_checks": {
            "reference": {
                "status": reference_status,
                "reason": reference_reason,
            },
            "amount": {
                "status": amount_status,
                "reason": amount_reason,
            },
            "supplier": {
                "status": supplier_status,
                "score": round(supplier_score, 4),
            },
        },
        "reasons": _questionable_match_reasons(
            match_status=match_status,
            reference_status=reference_status,
            reference_reason=reference_reason,
            amount_status=amount_status,
            amount_reason=amount_reason,
            supplier_status=supplier_status,
            supplier_score=supplier_score,
        ),
    }


def _questionable_match_reasons(
    *,
    match_status: str,
    reference_status: str,
    reference_reason: str | None,
    amount_status: str,
    amount_reason: str | None,
    supplier_status: str,
    supplier_score: float,
) -> list[str]:
    if match_status == "clean":
        return []

    reasons = []
    if reference_status == "missing":
        reasons.append(reference_reason or "invoice_id_missing_from_bank_reference")
    if amount_status in {"questionable", "mismatch", "missing"}:
        reasons.append(amount_reason or f"amount_{amount_status}")
    if reference_status == "missing" and supplier_status != "high":
        reasons.append(f"supplier_fuzzy_match_{supplier_status}:{supplier_score:.2f}")
    return reasons


def _reference_check(invoice_id: str | None, reference: str) -> tuple[str, str | None, float]:
    if not invoice_id:
        return "missing", None, 0.0

    normalized_reference = _normalize_identifier(reference)
    normalized_invoice_id = _normalize_identifier(invoice_id)
    if normalized_invoice_id and normalized_invoice_id in normalized_reference:
        return "exact", "invoice_id_found_in_bank_reference", 1.0
    return "missing", None, 0.0


def _amount_check(
    invoice: InvoiceMatchInput,
    transaction: BankTransaction,
) -> tuple[str, str | None, float]:
    if invoice.total_amount is None or invoice.total_amount <= 0:
        return "missing", None, 0.0

    if _amounts_close(transaction.amount, invoice.total_amount, AMOUNT_REVIEW_TOLERANCE):
        return "exact", "amount_within_tolerance", 1.0
    return "questionable", "amount_mismatch_requires_review", 0.5


def _supplier_score(supplier_name: str | None, counterparty: str) -> float:
    if not supplier_name or not counterparty:
        return 0.0
    return fuzz.token_set_ratio(_normalize_match_text(supplier_name), _normalize_match_text(counterparty)) / 100


def _supplier_status(supplier_score: float) -> str:
    if supplier_score >= SUPPLIER_HIGH_THRESHOLD:
        return "high"
    if supplier_score >= SUPPLIER_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _match_status(reference_status: str, amount_status: str, supplier_status: str) -> str:
    if reference_status == "exact" and amount_status == "exact":
        return "clean"
    if reference_status == "missing" and amount_status == "exact" and supplier_status == "high":
        return "clean"
    if reference_status == "exact" and amount_status == "questionable":
        return "questionable"
    if reference_status == "missing" and amount_status == "exact" and supplier_status == "medium":
        return "questionable"
    if reference_status == "missing" and amount_status == "questionable" and supplier_status == "high":
        return "questionable"
    return "none"


def _sort_score(reference_strength: float, amount_strength: float, supplier_score: float) -> float:
    return reference_strength * 0.5 + amount_strength * 0.35 + supplier_score * 0.15


def _parse_decimal(value: str | None) -> Decimal | None:
    normalized = normalize_amount(value)
    if normalized is None:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _amounts_close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    return abs(left - right) <= tolerance


def _normalize_identifier(value: str) -> str:
    normalized = value.upper().strip()
    normalized = normalized.replace("—", "-").replace("–", "-")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[^A-Z0-9-]", "", normalized)
    return normalized


def _normalize_match_text(value: str) -> str:
    normalized = value.upper()
    replacements = {
        "Ä": "AE",
        "Ö": "OE",
        "Ü": "UE",
        "ß": "SS",
        "Æ": "AE",
        "Ø": "O",
        "Å": "A",
    }
    for source, replacement in replacements.items():
        normalized = normalized.replace(source, replacement)
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    return " ".join(normalized.split())
