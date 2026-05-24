"""Evaluation harness for manually labelled invoice golden sets."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from rapidfuzz import fuzz

from invoice_extractor.extraction.llm import DEFAULT_MODEL, DEFAULT_REASONING_EFFORT
from invoice_extractor.core.models import ExtractedField, FieldStatus, InvoiceExtraction
from invoice_extractor.core.normalize import (
    normalize_amount,
    normalize_currency,
    normalize_date,
    normalize_percentage,
    normalize_quantity,
)
from invoice_extractor.extraction.pipeline import (
    DEFAULT_OCR_LANG,
    DEFAULT_USE_OCR,
    PipelineConfig,
    build_extraction_record,
)

ComparisonKind = Literal["exact", "fuzzy"]
Normalizer = Callable[[Any], str | None]

_MISSING = object()
_CONFIDENT_VALUES = {"high", "medium"}


@dataclass(frozen=True)
class FieldSpec:
    """How one golden field should be compared against an extracted value."""

    path: str
    normalizer: Normalizer
    comparison: ComparisonKind = "exact"
    fuzzy_threshold: int = 100
    missing_normalized_value: str | None = None


@dataclass(frozen=True)
class GoldenRecord:
    """One manually labelled invoice record."""

    document_name: str
    expected: Mapping[str, Any]


LINE_ITEM_MATCH_WEIGHTS = {
    "name": 0.35,
    "quantity": 0.15,
    "amount": 0.35,
    "currency": 0.15,
}


def load_golden_records(path: str | Path) -> list[GoldenRecord]:
    """Load one JSON object per line from the golden dataset."""

    records: list[GoldenRecord] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw_record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on golden line {line_number}: {exc}") from exc
        records.append(_parse_golden_record(raw_record, line_number=line_number))

    if not records:
        raise ValueError(f"No golden records found in {path}")
    return records


def score_pipeline_record(golden: GoldenRecord, record: Mapping[str, Any]) -> dict[str, Any]:
    """Score one extractor JSONL record against one golden record."""

    record_type = record.get("record_type")
    if record_type == "validated_extraction":
        extraction = InvoiceExtraction.model_validate(record["extraction"])
        document_result = score_document(golden, extraction)
        document_result["pipeline_record_type"] = "validated_extraction"
        document_result["candidate_validation"] = {
            "valid_fields": len(record.get("valid_fields", [])),
            "invalid_fields": len(record.get("invalid_fields", [])),
            "unchecked_fields": len(record.get("unchecked_fields", [])),
        }
        return document_result

    if record_type == "error":
        return score_document(
            golden,
            extraction=None,
            extraction_error={
                "reason": record.get("reason"),
                "attempted_steps": record.get("attempted_steps", []),
            },
        )

    raise ValueError(f"Unsupported extraction record_type: {record_type!r}")


def score_document(
    golden: GoldenRecord,
    extraction: InvoiceExtraction | None,
    *,
    extraction_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare one prediction against one golden record."""

    field_results: list[dict[str, Any]] = []
    label_warnings: list[str] = []

    for spec in DOCUMENT_FIELD_SPECS:
        expected = _get_nested_value(golden.expected, spec.path)
        if expected is _MISSING:
            label_warnings.append(f"missing_expected_field:{spec.path}")
            continue

        field = _get_extracted_field(extraction, spec.path) if extraction else None
        field_results.append(
            _score_field(
                document_name=golden.document_name,
                field_path=spec.path,
                spec=spec,
                expected=expected,
                field=field,
            )
        )

    expected_line_items = _get_nested_value(golden.expected, "line_items")
    if expected_line_items is _MISSING:
        label_warnings.append("missing_expected_field:line_items")
    elif not isinstance(expected_line_items, list):
        label_warnings.append("invalid_expected_field:line_items_must_be_list")
    else:
        field_results.extend(_score_line_items(golden, expected_line_items, extraction))

    document_result = {
        "document_name": golden.document_name,
        "pipeline_record_type": "error" if extraction_error else "validated_extraction",
        "field_results": field_results,
        "label_warnings": label_warnings,
    }
    if extraction_error:
        document_result["extraction_error"] = dict(extraction_error)
    return document_result


def build_report(document_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build CI-friendly summary metrics from document-level eval results."""

    all_field_results = [
        field_result
        for document_result in document_results
        for field_result in document_result["field_results"]
    ]
    summary = _summarize_field_results(all_field_results)
    summary["documents"] = len(document_results)
    summary["extraction_errors"] = sum(
        1 for document_result in document_results if "extraction_error" in document_result
    )

    field_metrics: dict[str, dict[str, Any]] = {}
    grouped_results: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for field_result in all_field_results:
        grouped_results[field_result["metric_key"]].append(field_result)

    for metric_key, results in sorted(grouped_results.items()):
        field_metrics[metric_key] = _summarize_field_results(results)

    failures = [
        field_result
        for field_result in all_field_results
        if field_result["outcome"] in {"hallucination", "miss", "label_error"}
    ]

    return {
        "summary": summary,
        "field_metrics": field_metrics,
        "failures": failures,
        "documents": list(document_results),
    }


def run_evaluation(
    *,
    golden_path: str | Path,
    pdf_dir: str | Path,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Run extraction for every golden record and return an evaluation report."""

    golden_records = load_golden_records(golden_path)
    pdf_directory = Path(pdf_dir)
    document_results = []

    for golden in golden_records:
        pdf_path = pdf_directory / golden.document_name
        if not pdf_path.exists():
            record = {
                "record_type": "error",
                "document_name": golden.document_name,
                "reason": f"PDF not found: {pdf_path}",
                "attempted_steps": [],
            }
        else:
            record = build_extraction_record(pdf_path, config=config)
        document_results.append(score_pipeline_record(golden, record))

    report = build_report(document_results)
    report["config"] = {
        "golden_path": str(golden_path),
        "pdf_dir": str(pdf_dir),
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "use_ocr": config.use_ocr,
        "ocr_lang": config.ocr_lang,
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="invoice-eval",
        description="Run extraction over a manually labelled golden set and report accuracy.",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("evals/golden_invoices.jsonl"),
        help="JSONL file with manually labelled invoice records.",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=Path("pdf_invoices"),
        help="Directory containing source invoice PDFs.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/eval_report.json"),
        help="Path to write the JSON evaluation report.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        help="OpenAI model to use for extraction.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["minimal", "none", "low", "medium", "high", "xhigh"],
        default=os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
        help="Reasoning effort for GPT-5-family models.",
    )
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--ocr",
        dest="use_ocr",
        action="store_true",
        default=DEFAULT_USE_OCR,
        help="Use Tesseract OCR when embedded PDF text is missing or sparse. Enabled by default.",
    )
    ocr_group.add_argument(
        "--no-ocr",
        dest="use_ocr",
        action="store_false",
        help="Disable Tesseract OCR fallback.",
    )
    parser.add_argument(
        "--tesseract-cmd",
        default="tesseract",
        help="Tesseract executable name or path.",
    )
    parser.add_argument(
        "--ocr-lang",
        default=DEFAULT_OCR_LANG,
        help="Tesseract language code, for example eng or eng+dan+deu.",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Exit non-zero when overall accuracy is below this 0..1 threshold.",
    )
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit non-zero when any document extraction fails.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = PipelineConfig(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        use_ocr=args.use_ocr,
        tesseract_cmd=args.tesseract_cmd,
        ocr_lang=args.ocr_lang,
    )

    report = run_evaluation(golden_path=args.golden, pdf_dir=args.pdf_dir, config=config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    _print_report_summary(report, args.out)
    if args.fail_under is not None and report["summary"]["accuracy"] < args.fail_under:
        raise SystemExit(1)
    if args.fail_on_errors and report["summary"]["extraction_errors"] > 0:
        raise SystemExit(1)


def _parse_golden_record(raw_record: Mapping[str, Any], *, line_number: int) -> GoldenRecord:
    document_name = raw_record.get("document_name")
    expected = raw_record.get("expected")
    if not isinstance(document_name, str) or not document_name:
        raise ValueError(f"Golden line {line_number} must include document_name")
    if not isinstance(expected, Mapping):
        raise ValueError(f"Golden line {line_number} must include expected object")

    return GoldenRecord(
        document_name=document_name,
        expected=expected,
    )


def _score_line_items(
    golden: GoldenRecord,
    expected_line_items: Sequence[Mapping[str, Any]],
    extraction: InvoiceExtraction | None,
) -> list[dict[str, Any]]:
    predicted_line_items = list(extraction.line_items) if extraction else []
    item_matches, unmatched_predicted_indexes = _match_line_items(
        expected_line_items,
        predicted_line_items,
    )
    results: list[dict[str, Any]] = []

    for expected_index, expected_item in enumerate(expected_line_items):
        predicted_index = item_matches.get(expected_index)
        predicted_item = (
            predicted_line_items[predicted_index] if predicted_index is not None else None
        )

        for field_name, base_spec in LINE_ITEM_FIELD_SPECS.items():
            field_path = f"line_items[{expected_index}].{field_name}"
            expected = expected_item.get(field_name, _MISSING)
            if expected is _MISSING:
                continue

            field = getattr(predicted_item, field_name) if predicted_item is not None else None
            spec = FieldSpec(
                field_path,
                base_spec.normalizer,
                comparison=base_spec.comparison,
                fuzzy_threshold=base_spec.fuzzy_threshold,
                missing_normalized_value=base_spec.missing_normalized_value,
            )
            results.append(
                _score_field(
                    document_name=golden.document_name,
                    field_path=field_path,
                    spec=spec,
                    expected=expected,
                    field=field,
                )
            )

    for predicted_index in sorted(unmatched_predicted_indexes):
        predicted_summary = _line_item_summary(predicted_line_items[predicted_index])
        if predicted_summary is None:
            continue
        results.append(
            {
                "document_name": golden.document_name,
                "field_path": f"line_items.extra[{predicted_index}]",
                "metric_key": "line_items.extra",
                "expected": None,
                "predicted": predicted_summary,
                "normalized_expected": None,
                "normalized_predicted": predicted_summary,
                "correct": False,
                "outcome": "hallucination",
                "reason": "unexpected_line_item",
                "confidence": "unknown",
                "comparison": "line_item_presence",
                "score": None,
            }
        )

    return results


def _score_field(
    *,
    document_name: str,
    field_path: str,
    spec: FieldSpec,
    expected: Any,
    field: ExtractedField | None,
) -> dict[str, Any]:
    predicted = _field_value(field)
    confidence = _field_confidence(field)
    metric_key = _metric_key(field_path)

    if spec.missing_normalized_value is not None:
        return _score_zero_equivalent_numeric_field(
            document_name=document_name,
            field_path=field_path,
            metric_key=metric_key,
            spec=spec,
            expected=expected,
            predicted=predicted,
            confidence=confidence,
        )

    if spec.missing_normalized_value is None and _is_missing(expected):
        if _is_missing(predicted):
            return _field_result(
                document_name,
                field_path,
                metric_key,
                expected,
                predicted,
                correct=True,
                outcome="correct_absent",
                reason="expected_and_predicted_missing",
                confidence=confidence,
                comparison=spec.comparison,
            )
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_predicted=spec.normalizer(predicted),
            correct=False,
            outcome="hallucination",
            reason="expected_missing_but_prediction_found",
            confidence=confidence,
            comparison=spec.comparison,
        )

    if spec.missing_normalized_value is None and _is_missing(predicted):
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_expected=spec.normalizer(expected),
            correct=False,
            outcome="miss",
            reason="expected_value_but_prediction_unknown",
            confidence=confidence,
            comparison=spec.comparison,
        )

    expected_normalized = _normalize_for_spec(spec, expected)
    predicted_normalized = _normalize_for_spec(spec, predicted)

    if expected_normalized is None:
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_predicted=predicted_normalized,
            correct=False,
            outcome="label_error",
            reason="expected_value_failed_normalization",
            confidence=confidence,
            comparison=spec.comparison,
        )

    if predicted_normalized is None:
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_expected=expected_normalized,
            correct=False,
            outcome="hallucination",
            reason="prediction_failed_normalization",
            confidence=confidence,
            comparison=spec.comparison,
        )

    if spec.comparison == "exact":
        passed = predicted_normalized == expected_normalized
        score = 1.0 if passed else 0.0
    else:
        score = fuzz.token_set_ratio(predicted_normalized, expected_normalized)
        passed = score >= spec.fuzzy_threshold

    return _field_result(
        document_name,
        field_path,
        metric_key,
        expected,
        predicted,
        normalized_expected=expected_normalized,
        normalized_predicted=predicted_normalized,
        correct=passed,
        outcome="correct" if passed else "hallucination",
        reason="values_match" if passed else "values_differ",
        confidence=confidence,
        comparison=spec.comparison,
        score=score,
    )


def _score_zero_equivalent_numeric_field(
    *,
    document_name: str,
    field_path: str,
    metric_key: str,
    spec: FieldSpec,
    expected: Any,
    predicted: Any,
    confidence: str,
) -> dict[str, Any]:
    expected_missing = _is_missing(expected)
    predicted_missing = _is_missing(predicted)
    expected_normalized = _normalize_for_spec(spec, expected)
    predicted_normalized = _normalize_for_spec(spec, predicted)
    zero_value = spec.missing_normalized_value

    if expected_normalized is None:
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_predicted=predicted_normalized,
            correct=False,
            outcome="label_error",
            reason="expected_value_failed_normalization",
            confidence=confidence,
            comparison=spec.comparison,
        )

    if predicted_normalized is None:
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_expected=expected_normalized,
            correct=False,
            outcome="hallucination",
            reason="prediction_failed_normalization",
            confidence=confidence,
            comparison=spec.comparison,
        )

    if predicted_missing and expected_normalized != zero_value:
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_expected=expected_normalized,
            normalized_predicted=predicted_normalized,
            correct=False,
            outcome="miss",
            reason="expected_nonzero_value_but_prediction_unknown",
            confidence=confidence,
            comparison=spec.comparison,
        )

    if expected_missing and predicted_normalized != zero_value:
        return _field_result(
            document_name,
            field_path,
            metric_key,
            expected,
            predicted,
            normalized_expected=expected_normalized,
            normalized_predicted=predicted_normalized,
            correct=False,
            outcome="hallucination",
            reason="expected_zero_value_but_prediction_nonzero",
            confidence=confidence,
            comparison=spec.comparison,
        )

    passed = predicted_normalized == expected_normalized
    return _field_result(
        document_name,
        field_path,
        metric_key,
        expected,
        predicted,
        normalized_expected=expected_normalized,
        normalized_predicted=predicted_normalized,
        correct=passed,
        outcome="correct" if passed else "hallucination",
        reason="values_match" if passed else "values_differ",
        confidence=confidence,
        comparison=spec.comparison,
        score=1.0 if passed else 0.0,
    )


def _field_result(
    document_name: str,
    field_path: str,
    metric_key: str,
    expected: Any,
    predicted: Any,
    *,
    correct: bool,
    outcome: str,
    reason: str,
    confidence: str,
    comparison: str,
    normalized_expected: str | None = None,
    normalized_predicted: str | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    return {
        "document_name": document_name,
        "field_path": field_path,
        "metric_key": metric_key,
        "expected": _stringify(expected),
        "predicted": _stringify(predicted),
        "normalized_expected": normalized_expected,
        "normalized_predicted": normalized_predicted,
        "correct": correct,
        "outcome": outcome,
        "reason": reason,
        "confidence": confidence,
        "comparison": comparison,
        "score": score,
    }


def _match_line_items(
    expected_line_items: Sequence[Mapping[str, Any]],
    predicted_line_items: Sequence[Any],
) -> tuple[dict[int, int | None], set[int]]:
    unmatched_predicted_indexes = set(range(len(predicted_line_items)))
    matches: dict[int, int | None] = {}

    for expected_index, expected_item in enumerate(expected_line_items):
        best_index = None
        best_score = 0.0
        for predicted_index in unmatched_predicted_indexes:
            score = _line_item_match_score(expected_item, predicted_line_items[predicted_index])
            if score > best_score:
                best_score = score
                best_index = predicted_index

        if best_index is not None and best_score > 0:
            matches[expected_index] = best_index
            unmatched_predicted_indexes.remove(best_index)
        else:
            matches[expected_index] = None

    return matches, unmatched_predicted_indexes


def _line_item_match_score(expected_item: Mapping[str, Any], predicted_item: Any) -> float:
    score = 0.0
    total_weight = 0.0

    for field_name, weight in LINE_ITEM_MATCH_WEIGHTS.items():
        spec = LINE_ITEM_FIELD_SPECS[field_name]
        expected = expected_item.get(field_name, _MISSING)
        if expected is _MISSING or (
            spec.missing_normalized_value is None and _is_missing(expected)
        ):
            continue

        total_weight += weight
        predicted = _field_value(getattr(predicted_item, field_name))
        expected_normalized = _normalize_for_spec(spec, expected)
        predicted_normalized = _normalize_for_spec(spec, predicted)
        if expected_normalized is None or predicted_normalized is None:
            continue

        if spec.comparison == "fuzzy":
            score += weight * (fuzz.token_set_ratio(predicted_normalized, expected_normalized) / 100)
        elif expected_normalized == predicted_normalized:
            score += weight

    return score / total_weight if total_weight else 0.0


def _summarize_field_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(results)
    correct = sum(1 for result in results if result["correct"])
    hallucinations = sum(1 for result in results if result["outcome"] == "hallucination")
    misses = sum(1 for result in results if result["outcome"] == "miss")
    label_errors = sum(1 for result in results if result["outcome"] == "label_error")
    correct_absent = sum(1 for result in results if result["outcome"] == "correct_absent")
    confident_hallucinations = sum(
        1
        for result in results
        if result["outcome"] == "hallucination" and result["confidence"] in _CONFIDENT_VALUES
    )

    return {
        "total": total,
        "correct": correct,
        "correct_absent": correct_absent,
        "hallucinations": hallucinations,
        "confident_hallucinations": confident_hallucinations,
        "misses": misses,
        "label_errors": label_errors,
        "accuracy": correct / total if total else 0.0,
    }


def _get_nested_value(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _get_extracted_field(extraction: InvoiceExtraction | None, path: str) -> ExtractedField | None:
    current: Any = extraction
    for part in path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current if isinstance(current, ExtractedField) else None


def _field_value(field: ExtractedField | None) -> str | None:
    if field is None or field.status == FieldStatus.UNKNOWN:
        return None
    return field.normalized_value or field.raw_value


def _field_confidence(field: ExtractedField | None) -> str:
    if field is None:
        return "unknown"
    return str(field.confidence)


def _line_item_summary(item: Any) -> str | None:
    parts = []
    for field_name in LINE_ITEM_FIELD_SPECS:
        value = _field_value(getattr(item, field_name))
        if value is not None:
            parts.append(f"{field_name}={value}")
    return "; ".join(parts) if parts else None


def _metric_key(field_path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", field_path)


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _stringify(value: Any) -> str | None:
    if _is_missing(value):
        return None
    return str(value)


def _normalize_for_spec(spec: FieldSpec, value: Any) -> str | None:
    if _is_missing(value) and spec.missing_normalized_value is not None:
        return spec.missing_normalized_value
    return spec.normalizer(value)


def _normalize_text(value: Any, *, uppercase: bool = False) -> str | None:
    text = _stringify(value)
    if text is None:
        return None
    normalized = " ".join(text.strip().split())
    return normalized.upper() if uppercase else normalized


def _normalize_name(value: Any) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    normalized = re.sub(r"[^\w\s&/+.-]", " ", text.lower())
    return " ".join(normalized.split())


def _normalize_currency_value(value: Any) -> str | None:
    return normalize_currency(_stringify(value))


def _normalize_date_value(value: Any) -> str | None:
    return normalize_date(_stringify(value))


def _normalize_amount_value(value: Any) -> str | None:
    return normalize_amount(_stringify(value))


def _normalize_percentage_value(value: Any) -> str | None:
    return normalize_percentage(_stringify(value))


def _normalize_quantity_value(value: Any) -> str | None:
    return normalize_quantity(_stringify(value))


DOCUMENT_FIELD_SPECS = (
    FieldSpec("invoice_id", lambda value: _normalize_text(value, uppercase=True)),
    FieldSpec("supplier_name", _normalize_name, comparison="fuzzy", fuzzy_threshold=90),
    FieldSpec("currency", _normalize_currency_value),
    FieldSpec("invoice_date", _normalize_date_value),
    FieldSpec("due_date", _normalize_date_value),
    FieldSpec("po_reference", lambda value: _normalize_text(value, uppercase=True)),
    FieldSpec("totals.pre_tax_amount", _normalize_amount_value, missing_normalized_value="0.00"),
    FieldSpec("totals.tax_percentage", _normalize_percentage_value, missing_normalized_value="0.00"),
    FieldSpec("totals.tax_amount", _normalize_amount_value, missing_normalized_value="0.00"),
    FieldSpec("totals.discount", _normalize_amount_value, missing_normalized_value="0.00"),
    FieldSpec("totals.total_amount", _normalize_amount_value, missing_normalized_value="0.00"),
)

LINE_ITEM_FIELD_SPECS = {
    "name": FieldSpec("line_items[].name", _normalize_name, comparison="fuzzy", fuzzy_threshold=85),
    "quantity": FieldSpec(
        "line_items[].quantity",
        _normalize_quantity_value,
        missing_normalized_value="0",
    ),
    "amount": FieldSpec(
        "line_items[].amount",
        _normalize_amount_value,
        missing_normalized_value="0.00",
    ),
    "currency": FieldSpec("line_items[].currency", _normalize_currency_value),
}


def _print_report_summary(report: Mapping[str, Any], output_path: Path) -> None:
    summary = report["summary"]
    print(
        "Evaluated "
        f"{summary['documents']} documents: "
        f"{summary['correct']}/{summary['total']} correct fields, "
        f"{summary['accuracy']:.1%} accuracy, "
        f"{summary['hallucinations']} hallucinations, "
        f"{summary['misses']} misses, "
        f"{summary['extraction_errors']} extraction errors."
    )
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
