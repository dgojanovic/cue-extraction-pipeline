# Invoice Extraction POC

Original task prompt: [TASK.md](/Users/domagojgojanovicbozic/Repositories/tasks/NER-challange/TASK.md)

## Setup

```bash
uv sync
cp .env.example .env
```

Set:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
OPENAI_REASONING_EFFORT=minimal
```

Install Tesseract OCR if you want OCR fallback for scanned PDFs. OCR is enabled by default with `eng+dan+deu`; pass `--no-ocr` to disable it.

## Approach

The extractor sends the original PDF directly to the LLM because invoice layout and multilingual text are variable, and file input preserves more document context than pre-flattened text. Local text extraction/OCR is still used to build deterministic candidates for validation, not as prompt input. The tradeoff is extra cost and latency from direct PDF calls, but better robustness on layout-heavy invoices; OCR/candidate validation adds grounding but can miss values if OCR or regex extraction is incomplete.

Model confidence is not treated as calibrated. Trust is derived from schema checks, normalization, candidate validation against PDF/OCR text, total integrity checks, and downstream triage signals. Output fields are split into `valid_fields`, `invalid_fields`, and `unchecked_fields`.

## Task 1: Extraction

Run extraction for all PDFs:

```bash
.venv/bin/python -m invoice_extractor.cli extract
```

Result:

- `outputs/extractions.jsonl`
- each successful record has `record_type: "validated_extraction"`
- failed PDFs produce `record_type: "error"` with `reason` and `attempted_steps`
- fields are split into `valid_fields`, `invalid_fields`, and `unchecked_fields`

Optional inspection commands:

```bash
.venv/bin/python -m invoice_extractor.cli inspect-text
```

```bash
.venv/bin/python -m invoice_extractor.cli inspect-candidates
```

## Task 2: Evaluation

Run the golden-set evaluation:

```bash
.venv/bin/python evals/evaluator.py
```

Inputs:

- `evals/golden_invoices.jsonl`
- `pdf_invoices/`

Result:

- `outputs/eval_report.json`
- per-field accuracy
- exact matching for IDs, dates, currencies, totals, percentages, and quantities
- fuzzy matching for supplier names and line-item names
- separate counts for hallucinations, misses, and label errors

String equality is insufficient for supplier and line-item names because punctuation, accents, abbreviations, and token order can vary while still referring to the same value.

Current local result:

```text
5 documents, 98/99 correct fields, 98.99% accuracy,
1 hallucination, 0 misses, 0 extraction errors.
```

## Task 3: Triage

Run extraction first, then triage:

```bash
.venv/bin/python -m invoice_extractor.cli triage
```

Inputs:

- `outputs/extractions.jsonl`
- `bank_transactions.csv`

Result:

- `outputs/triage_report.jsonl`
- one decision per invoice
- outcomes: `auto_accept`, `review`, `reject`
- reasons included for `review` and `reject`
- matched bank transactions include separate invoice-id, amount, and supplier checks

Current local result:

```text
12 invoices: 4 auto-accept, 8 review, 0 reject.
```

Triage thresholds and decision tree: [docs/triage.md](/Users/domagojgojanovicbozic/Repositories/tasks/NER-challange/docs/triage.md)

## Task 4: Design

Production design note:

- [docs/DESIGN.md](/Users/domagojgojanovicbozic/Repositories/tasks/NER-challange/docs/DESIGN.md)

It covers production rollout, queues vs synchronous work, human review, model rollouts, model-selection levers, and deterministic/LLM/human boundaries.

## Development

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Current status:

```text
39 tests passed
ruff: all checks passed
```

## Notes

- Generated outputs are written under `outputs/` and are ignored by git.
- `invoices.csv` is not used as ground truth.
- OCR is used for local text/candidate extraction when embedded text is sparse; the LLM receives the original PDF.
- Complex payment explanations such as FX conversion, early-payment discounts, and batch payments are not modeled explicitly. Amount mismatches over tolerance route to review.
