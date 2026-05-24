"""Regex-based candidate extraction for grounding LLM invoice extraction."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from invoice_extractor.core.normalize import (
    normalize_amount,
    normalize_currency,
    normalize_date,
    normalize_percentage,
)

_CONTEXT_CHARS = 70

_DATE_RE = re.compile(r"\b(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[./-]\d{2}[./-]\d{4})\b")
_CURRENCY_RE = re.compile(r"\b(?:DKK|EUR|USD)\b|[$€]|\bkr\.?\b", re.IGNORECASE)
_TAX_PERCENTAGE_RE = re.compile(r"\b\d{1,2}(?:[,.]\d+)?\s*%", re.IGNORECASE)
_ID_CANDIDATE_RE = re.compile(
    r"\b(?:[A-Z]{2,5}-\d{2,4}(?:-\d{2,5})?|[A-Z]{2,5}-\d{3,6}|\d{4}-\d{4,6})\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"""
    (?<![\w])
    (?:
        (?:DKK|EUR|USD|kr\.?|[$€])\s*
    )?
    -?\(?\d{1,3}(?:[ .,']?\d{3})*(?:[,.]\d{2})\)?
    (?:\s*(?:DKK|EUR|USD|kr\.?|[$€]))?
    (?![\w])
    """,
    re.IGNORECASE | re.VERBOSE,
)


class Candidate(BaseModel):
    """A grounded value candidate found in invoice text."""

    raw_value: str
    normalized_value: str | date | Decimal | None = None


class TextCandidate(Candidate):
    """Candidate that normalizes to a string identifier/reference."""

    normalized_value: str


class DateCandidate(Candidate):
    """Candidate that normalizes to a calendar date."""

    normalized_value: date


class CurrencyCandidate(Candidate):
    """Candidate that normalizes to an ISO currency code."""

    normalized_value: str


class AmountCandidate(Candidate):
    """A money-like candidate plus its inferred invoice role."""

    normalized_value: Decimal
    currency: str | None = None
    role: str = "unknown"


class PercentageCandidate(Candidate):
    """Candidate that normalizes to a decimal percentage value."""

    normalized_value: Decimal


class ExtractionCandidates(BaseModel):
    """Candidate values extracted before the LLM step."""

    document_name: str | None = None
    id_candidates: list[TextCandidate] = Field(default_factory=list)
    dates: list[DateCandidate] = Field(default_factory=list)
    currencies: list[CurrencyCandidate] = Field(default_factory=list)
    amounts: list[AmountCandidate] = Field(default_factory=list)
    tax_percentages: list[PercentageCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def extract_candidates(text: str, *, document_name: str | None = None) -> ExtractionCandidates:
    """Extract high-recall candidate values from invoice text."""

    warnings = []
    if not text.strip():
        warnings.append("no_text_for_candidate_extraction")

    return ExtractionCandidates(
        document_name=document_name,
        id_candidates=_extract_id_candidates(text),
        dates=_extract_dates(text),
        currencies=_extract_currencies(text),
        amounts=_extract_amounts(text),
        tax_percentages=_extract_tax_percentages(text),
        warnings=warnings,
    )


def _extract_id_candidates(text: str) -> list[TextCandidate]:
    candidates = []
    for match in _ID_CANDIDATE_RE.finditer(text):
        raw_value = match.group(0).upper()

        candidates.append(
            TextCandidate(
                raw_value=raw_value,
                normalized_value=raw_value,
            )
        )

    return _dedupe_candidates(candidates)


def _extract_dates(text: str) -> list[DateCandidate]:
    candidates = []
    for match in _DATE_RE.finditer(text):
        raw_value = match.group(0)
        normalized_value = normalize_date(raw_value)
        if normalized_value is None:
            continue

        candidates.append(
            DateCandidate(
                raw_value=raw_value,
                normalized_value=date.fromisoformat(normalized_value),
            )
        )

    return _dedupe_candidates(candidates)


def _extract_currencies(text: str) -> list[CurrencyCandidate]:
    candidates = []
    for match in _CURRENCY_RE.finditer(text):
        raw_value = match.group(0)
        normalized_value = normalize_currency(raw_value)
        if normalized_value is None:
            continue

        candidates.append(
            CurrencyCandidate(
                raw_value=raw_value,
                normalized_value=normalized_value,
            )
        )

    return _dedupe_candidates(candidates)


def _extract_amounts(text: str) -> list[AmountCandidate]:
    candidates = []
    for match in _AMOUNT_RE.finditer(text):
        raw_value = match.group(0).strip()
        normalized_value = normalize_amount(raw_value)
        if normalized_value is None:
            continue

        context = _context(text, match.start(), match.end())
        candidates.append(
            AmountCandidate(
                raw_value=raw_value,
                normalized_value=Decimal(normalized_value),
                currency=_infer_currency(raw_value, context),
            )
        )

    return _dedupe_amounts(candidates)


def _extract_tax_percentages(text: str) -> list[PercentageCandidate]:
    candidates = []
    for match in _TAX_PERCENTAGE_RE.finditer(text):
        raw_value = match.group(0)
        normalized_value = normalize_percentage(raw_value)
        if normalized_value is None:
            continue

        candidates.append(
            PercentageCandidate(
                raw_value=raw_value,
                normalized_value=Decimal(normalized_value),
            )
        )

    return _dedupe_candidates(candidates)


def _infer_currency(raw_value: str, context: str) -> str | None:
    return normalize_currency(raw_value) or normalize_currency(context)


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - _CONTEXT_CHARS)
    right = min(len(text), end + _CONTEXT_CHARS)
    return " ".join(text[left:right].split())


def _dedupe_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen = set()
    deduped = []

    for candidate in candidates:
        key = candidate.normalized_value or candidate.raw_value
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    return deduped


def _dedupe_amounts(candidates: Iterable[AmountCandidate]) -> list[AmountCandidate]:
    seen = set()
    deduped = []

    for candidate in candidates:
        key = (candidate.normalized_value or candidate.raw_value, candidate.role)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    return deduped
