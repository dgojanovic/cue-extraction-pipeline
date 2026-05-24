# Task 3 Triage Decision Tree

This pipeline routes each extracted invoice to `auto_accept`, `review`, or `reject` by combining extraction quality checks with deterministic bank-transaction matching.

Run it with:

```bash
.venv/bin/python -m invoice_extractor.cli triage \
  --extractions outputs/extractions.jsonl \
  --bank bank_transactions.csv \
  --out outputs/triage_report.jsonl
```

## Inputs

- `outputs/extractions.jsonl`: validated extraction records from Task 1.
- `bank_transactions.csv`: outgoing bank transactions.

The matcher uses only:

- extracted `invoice_id`
- extracted `supplier_name`
- extracted `currency`
- extracted `totals.total_amount`
- bank `amount`
- bank `counterparty`
- bank `reference`

The bank `category` column is included in output for visibility only. It is not used for matching or routing.

## Thresholds

| Signal | Threshold | Meaning |
| --- | ---: | --- |
| Amount | `abs(bank_amount - invoice_total) <= 1.00` | Amount is considered exact enough for auto-accept logic. |
| Supplier high | `>= 0.75` | Supplier names are close enough to be a strong fallback when invoice ID is missing from bank reference. |
| Supplier medium | `>= 0.50` and `< 0.75` | Supplier is plausible but not strong enough for fallback auto-accept. |
| Supplier low | `< 0.50` | Supplier is weak. |

Supplier matching uses `rapidfuzz.fuzz.token_set_ratio` after normalizing case, accents, punctuation, and whitespace.

Invoice ID matching is exact only: the normalized extracted invoice ID must appear in the normalized bank reference. We do not currently use partial ID matching, leading-zero variants, or fuzzy ID matching.

These thresholds are intentionally pragmatic rather than statistically calibrated. The golden set is very small, so a single unusual supplier abbreviation, OCR artifact, or payment note can move the observed accuracy by a large percentage. That makes it risky to claim that `0.75` is a universally correct supplier threshold or that `1.00` is the right amount tolerance for every customer. In a production rollout, these thresholds should be monitored against a larger labelled set and adjusted per tenant/payment process.

## Signal Checks

Each candidate bank transaction gets separate signal checks:

```json
{
  "signal_checks": {
    "reference": {
      "status": "exact",
      "reason": "invoice_id_found_in_bank_reference"
    },
    "amount": {
      "status": "exact",
      "reason": "amount_within_tolerance"
    },
    "supplier": {
      "status": "high",
      "score": 0.8
    }
  }
}
```

Reference statuses:

- `exact`: extracted invoice ID appears exactly in bank reference after normalization.
- `missing`: extracted invoice ID does not appear exactly in bank reference.

Amount statuses:

- `exact`: bank amount and invoice amount differ by at most `1.00`.
- `questionable`: bank amount and invoice amount differ by more than `1.00`.
- `missing`: invoice amount is missing or not positive.

Supplier statuses:

- `high`: fuzzy score is at least `0.75`.
- `medium`: fuzzy score is at least `0.50` and below `0.75`.
- `low`: fuzzy score is below `0.50`.

## Decision Tree

```text
Start with one extraction record.
```

```text
1. Did extraction fail?
   ├─ Yes -> REJECT
   └─ No  -> continue
```

```text
2. Are critical extraction fields missing or invalid?

   Critical required fields:
   - invoice_id
   - supplier_name
   - currency
   - totals.total_amount

   Candidate-validated critical fields:
   - invoice_id
   - currency
   - totals.total_amount

   ├─ Yes -> REJECT
   └─ No  -> continue
```

```text
3. Score every bank transaction with independent checks:

   reference: exact | missing
   amount:    exact | questionable | missing
   supplier:  high | medium | low
```

```text
4. Is a bank transaction clean?

   Clean when:
   - reference exact AND amount exact
   - OR reference missing AND amount exact AND supplier high

   ├─ Yes -> candidate match_status = clean
   └─ No  -> continue
```

```text
5. Is a bank transaction still plausible but questionable?

   Questionable when:
   - reference exact AND amount questionable
   - OR reference missing AND amount exact AND supplier medium
   - OR reference missing AND amount questionable AND supplier high

   ├─ Yes -> candidate match_status = questionable
   └─ No  -> no plausible match
```

```text
6. No plausible bank match?
   ├─ Yes -> REJECT
   └─ No  -> continue
```

```text
7. Extraction confidence high?
   ├─ No  -> REVIEW
   └─ Yes -> continue
```

```text
8. Best bank match clean?
   ├─ Yes -> AUTO_ACCEPT
   └─ No  -> REVIEW
```

## Outcomes

### Auto-Accept

An invoice is auto-accepted when extraction confidence is high and the best bank match is clean.

Clean bank match means either:

- exact invoice ID in bank reference and amount within `±1.00`, or
- invoice ID missing from bank reference, amount within `±1.00`, and supplier match is high.

Top-level `reasons` are empty for auto-accepted records. Positive details are still visible in `matched_transactions[].signal_checks`.

### Review

An invoice is routed to review when there is a plausible bank match but at least one signal is questionable, or extraction confidence is below the auto-accept threshold.

Review reasons are listed below.

### Reject

An invoice is rejected when extraction failed, critical extracted data is missing or invalid, or no plausible bank match exists.

Reject reasons are listed below.

## Review Reasons

| Reason | Meaning |
| --- | --- |
| `amount_mismatch_requires_review` | Bank amount and invoice total differ by more than `1.00`. We do not try to explain FX, early-payment discounts, or batch payments in this POC; amount mismatch is simply review-worthy. |
| `invoice_id_missing_from_bank_reference` | The exact extracted invoice ID does not appear in the bank reference. The transaction may still be plausible through amount and supplier. |
| `supplier_fuzzy_match_medium:<score>` | Supplier match is plausible but below the `0.75` high threshold. |
| `supplier_fuzzy_match_low:<score>` | Supplier match is weak. This usually prevents a plausible match unless invoice ID and amount are strong enough. |
| `match_status_questionable` | The best bank match is plausible but not clean. This is a summary routing reason. |
| `multiple_candidate_matches` | More than one bank transaction scored close enough to the best candidate that picking one automatically would be risky. |
| `extraction_confidence_below_auto_accept_threshold` | Extraction did not meet high-confidence requirements, even though a bank match exists. |
| `critical_candidate_validation_missing:<fields>` | One or more candidate-validated critical fields did not receive deterministic candidate confirmation. This lowers extraction confidence. |

## Reject Reasons

| Reason | Meaning |
| --- | --- |
| `extraction_failed` | The extractor produced an error record for the invoice. |
| `unsupported_extraction_record_type:<type>` | The triage input record was neither `validated_extraction` nor `error`. |
| `missing_invoice_id` | Extracted invoice ID is missing. |
| `missing_supplier_name` | Extracted supplier name is missing. |
| `missing_currency` | Extracted currency is missing. |
| `missing_total_amount` | Extracted invoice total is missing. |
| `unsupported_currency` | Extracted currency is not supported by the pipeline. |
| `critical_field_failed_validation:<fields>` | Candidate validation rejected a critical field such as `invoice_id`, `currency`, or `totals.total_amount`. |
| `total_integrity_failed` | Extracted totals do not reconcile: `pre_tax + tax - discount` differs from `total` by more than the allowed rounding tolerance. |
| `no_plausible_bank_match` | No bank transaction met even the questionable-match rules. |

## Notes

The matcher deliberately avoids trying to infer complex payment explanations. FX conversion, early-payment discounts, and batch payments are not modeled explicitly. If they cause the amount to differ by more than `1.00`, the invoice is routed to review with `amount_mismatch_requires_review`.

OCR use is kept in the extraction record's `source_warnings`. It does not by itself force review or appear as a triage reason when the extraction and bank match are otherwise clean.

This is intentionally conservative: strong deterministic signals can auto-accept simple cases, while anything ambiguous goes to human review.
