from datetime import date
from decimal import Decimal

from invoice_extractor.candidates import extract_candidates


def test_extract_candidates_finds_core_invoice_values() -> None:
    text = """
    NORDIC STEEL A/S
    Fakturanummer:
    NS-2026-0431
    Fakturadato:
    2026-04-02
    Forfaldsdato:
    2026-05-02
    Kundens ref.:
    PO-44521
    Subtotal ekskl. moms
    9.960,00 DKK
    Moms 25%
    2.490,00 DKK
    Total at betale
    12.450,00 DKK
    """

    candidates = extract_candidates(text, document_name="invoice_01.pdf")

    assert candidates.document_name == "invoice_01.pdf"
    assert "NS-2026-0431" in {
        candidate.normalized_value for candidate in candidates.id_candidates
    }
    assert {candidate.normalized_value for candidate in candidates.dates} == {
        date(2026, 4, 2),
        date(2026, 5, 2),
    }
    assert {candidate.normalized_value for candidate in candidates.currencies} == {"DKK"}
    assert {candidate.normalized_value for candidate in candidates.tax_percentages} == {
        Decimal("25.00")
    }
    assert Decimal("12450.00") in {
        candidate.normalized_value for candidate in candidates.amounts
    }
    assert {candidate.role for candidate in candidates.amounts} == {"unknown"}


def test_extract_candidates_keeps_reference_like_id_candidates() -> None:
    text = """
    Rechnung
    Rechnungs-Nr.:
    RE-2026-339
    Lieferschein-Nr.:
    LS-5680
    Auftragsnummer:
    AU-2026-4773
    Gesamtbetrag:
    5.680,50 EUR
    """

    candidates = extract_candidates(text)

    assert {candidate.normalized_value for candidate in candidates.id_candidates} == {
        "RE-2026-339",
        "LS-5680",
        "AU-2026-4773",
    }
    assert Decimal("5680.50") in {candidate.normalized_value for candidate in candidates.amounts}


def test_extract_candidates_keeps_po_like_invoice_and_order_candidates() -> None:
    text = """
    Supplier Example
    Invoice number
    PO-98765
    Purchase order
    PO-44521
    Total due
    DKK 100.00
    """

    candidates = extract_candidates(text)

    assert {candidate.normalized_value for candidate in candidates.id_candidates} == {
        "PO-98765",
        "PO-44521",
    }


def test_extract_candidates_keeps_non_po_prefixed_reference_candidates() -> None:
    text = """
    Bestellnummer:
    BN-77441
    Auftragsnummer:
    AU-2026-4773
    Customer reference
    REF-9912
    """

    candidates = extract_candidates(text)

    assert {candidate.normalized_value for candidate in candidates.id_candidates} == {
        "BN-77441",
        "AU-2026-4773",
        "REF-9912",
    }


def test_extract_candidates_keeps_delivery_note_id_like_candidates() -> None:
    text = """
    Rechnung
    Rechnungsnummer:
    RE-2026-1198
    Maschinenbauteile gem. Lieferschein LS-2026-441
    Gesamtbetrag:
    8.750,00 EUR
    """

    candidates = extract_candidates(text)

    assert {candidate.normalized_value for candidate in candidates.id_candidates} == {
        "RE-2026-1198",
        "LS-2026-441",
    }


def test_extract_candidates_keeps_product_codes_that_look_like_invoice_ids() -> None:
    text = """
    INVOICE
    INV-44280
    Industrial Drill, HD-450
    TOTAL DUE
    USD 5,120.75
    """

    candidates = extract_candidates(text)

    assert {candidate.normalized_value for candidate in candidates.id_candidates} == {
        "INV-44280",
        "HD-450",
    }


def test_extract_candidates_handles_empty_text() -> None:
    candidates = extract_candidates("")

    assert candidates.id_candidates == []
    assert candidates.amounts == []
    assert candidates.warnings == ["no_text_for_candidate_extraction"]


def test_extract_candidates_keeps_non_tax_percentages() -> None:
    text = """
    Payment terms
    Late fee 2% per month
    VAT 25%
    """

    candidates = extract_candidates(text)

    assert {candidate.normalized_value for candidate in candidates.tax_percentages} == {
        Decimal("2.00"),
        Decimal("25.00"),
    }
