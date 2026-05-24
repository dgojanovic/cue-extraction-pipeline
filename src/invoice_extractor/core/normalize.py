"""Deterministic normalization helpers for extracted invoice values."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SUPPORTED_CURRENCIES = frozenset({"DKK", "EUR", "USD"})
_MONEY_QUANT = Decimal("0.01")
_NUMBER_RE = re.compile(r"-?\(?\d[\d\s.,']*\)?")


def normalize_currency(value: str | None, default: str | None = None) -> str | None:
    """Return a supported ISO currency code, or a default when the value is blank."""

    if value is None or not value.strip():
        return default if default in SUPPORTED_CURRENCIES else None

    normalized = value.upper().strip()
    symbol_map = {
        "€": "EUR",
        "$": "USD",
        "US$": "USD",
        "KR": "DKK",
        "KR.": "DKK",
    }

    if normalized in SUPPORTED_CURRENCIES:
        return normalized
    if normalized in symbol_map:
        return symbol_map[normalized]
    if "€" in normalized:
        return "EUR"
    if "$" in normalized:
        return "USD"
    if re.search(r"\bKR\.?\b", normalized):
        return "DKK"

    for currency in SUPPORTED_CURRENCIES:
        if re.search(rf"\b{currency}\b", normalized):
            return currency

    return None


def normalize_date(value: str | None) -> str | None:
    """Normalize common invoice date formats to ISO YYYY-MM-DD."""

    if value is None or not value.strip():
        return None

    cleaned = value.strip()
    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %m %Y",
    )

    for date_format in formats:
        try:
            return datetime.strptime(cleaned, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def normalize_amount(value: str | int | float | Decimal | None) -> str | None:
    """Normalize common European/US amount strings to a two-decimal string."""

    decimal_value = _parse_decimal(value)
    if decimal_value is None:
        return None
    return _format_decimal(decimal_value, decimal_places=2)


def normalize_percentage(value: str | int | float | Decimal | None) -> str | None:
    """Normalize a percentage value without the percent sign."""

    decimal_value = _parse_decimal(value)
    if decimal_value is None:
        return None
    return _format_decimal(decimal_value, decimal_places=2)


def normalize_discount(value: str | int | float | Decimal | None) -> str | None:
    """Normalize a discount while preserving whether it was a percentage."""

    if isinstance(value, str) and "%" in value:
        percentage = normalize_percentage(value)
        return f"{percentage}%" if percentage is not None else None
    return normalize_amount(value)


def normalize_quantity(value: str | int | float | Decimal | None) -> str | None:
    """Normalize a line-item quantity without forcing currency-style decimals."""

    decimal_value = _parse_decimal(value)
    if decimal_value is None:
        return None

    normalized = decimal_value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def _parse_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))

    raw = value.strip()
    if not raw:
        return None

    match = _NUMBER_RE.search(_strip_currency_words(raw))
    if match is None:
        return None

    cleaned = match.group(0).replace(" ", "").replace("'", "")
    is_parenthesized_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")

    normalized = _normalize_number_separators(cleaned)
    if normalized is None:
        return None

    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation:
        return None

    if is_parenthesized_negative and decimal_value > 0:
        decimal_value = -decimal_value
    return decimal_value


def _strip_currency_words(value: str) -> str:
    stripped = value.upper()
    stripped = re.sub(r"\b(DKK|EUR|USD|KR\.?|VAT|MOMS)\b", " ", stripped)
    stripped = stripped.replace("€", " ").replace("$", " ")
    return stripped


def _normalize_number_separators(value: str) -> str | None:
    if not any(character.isdigit() for character in value):
        return None

    comma_count = value.count(",")
    dot_count = value.count(".")

    if comma_count and dot_count:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        return value.replace(thousands_separator, "").replace(decimal_separator, ".")

    if comma_count:
        return _normalize_single_separator(value, ",")

    if dot_count:
        return _normalize_single_separator(value, ".")

    return value


def _normalize_single_separator(value: str, separator: str) -> str:
    parts = value.split(separator)
    last_part = parts[-1]

    if len(parts) == 2 and len(last_part) in {1, 2}:
        return value.replace(separator, ".")

    if len(parts) > 2 and len(last_part) == 2:
        return "".join(parts[:-1]) + "." + last_part

    return "".join(parts)


def _format_decimal(value: Decimal, decimal_places: int) -> str:
    quant = Decimal("1").scaleb(-decimal_places)
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(rounded, f".{decimal_places}f")
