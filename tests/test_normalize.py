from invoice_extractor.normalize import (
    normalize_amount,
    normalize_currency,
    normalize_date,
    normalize_discount,
    normalize_percentage,
    normalize_quantity,
)


def test_normalize_amount_handles_us_and_european_formats() -> None:
    assert normalize_amount("1,234.56") == "1234.56"
    assert normalize_amount("1.234,56") == "1234.56"
    assert normalize_amount("DKK 1234.5") == "1234.50"
    assert normalize_amount("8.750,00") == "8750.00"


def test_normalize_amount_handles_thousands_and_negative_values() -> None:
    assert normalize_amount("4,200") == "4200.00"
    assert normalize_amount("(1,234.56)") == "-1234.56"
    assert normalize_amount("-85") == "-85.00"


def test_normalize_date_accepts_common_invoice_formats() -> None:
    assert normalize_date("2026-04-07") == "2026-04-07"
    assert normalize_date("05/04/2026") == "2026-04-05"
    assert normalize_date("14-04-2026") == "2026-04-14"
    assert normalize_date("14.04.2026") == "2026-04-14"


def test_normalize_currency_accepts_codes_and_common_symbols() -> None:
    assert normalize_currency("dkk") == "DKK"
    assert normalize_currency("€") == "EUR"
    assert normalize_currency("Gesamt €") == "EUR"
    assert normalize_currency("kr.") == "DKK"
    assert normalize_currency("", default="DKK") == "DKK"
    assert normalize_currency("GBP") is None


def test_normalize_percentage_discount_and_quantity() -> None:
    assert normalize_percentage("VAT 25%") == "25.00"
    assert normalize_discount("2%") == "2.00%"
    assert normalize_discount("DKK 50") == "50.00"
    assert normalize_quantity("2.00") == "2"
    assert normalize_quantity("1,5") == "1.5"
