# Cue — AI Engineer Take-Home Exercise

## Overview

You are building a proof-of-concept extraction and reconciliation pipeline for a mid-sized distributor. They receive supplier invoices in many formats, mostly PDFs with no consistent template, and need them turned into structured records that can be matched against outgoing bank payments and reviewed by their finance team.

The interesting problem here is reliably pulling structured data out of unstructured supplier documents with an LLM, knowing when to trust the result, and proving it with numbers. We care more about your evaluation methodology and how you reason about LLM behaviour than about squeezing the last point of accuracy out of an extractor.

This exercise is designed to take approximately **3–4 hours**. We value working software, clear thinking, and pragmatic tradeoffs over perfection.

> **If you run short on time, prioritize a thoughtful extraction + evaluation loop (Tasks 1–2) over breadth.** A narrow solution with clear metrics and failure analysis beats a complete but unvalidated demo. Submit what you have with notes on what you would do next.

## Context

The customer's current process is manual: a finance team member opens each PDF invoice, types the relevant fields into a spreadsheet, and reconciles it against the bank statement. This takes roughly 2 full days per month and is error-prone.

You have been given one month of data: a folder of supplier invoice PDFs, a CSV that represents the finance team's manually-typed view of those invoices, and a CSV of outgoing bank transactions.

### Data Files

#### `pdf_invoices/`

Approximately 12 supplier invoice PDFs. These are digitally-generated invoices with no consistent layout. Each supplier uses their own template, some are in Danish, some in German, some in English. At least one is scanned (image-only) rather than text-native.

You do not need to optimize for all possible invoice formats. Focus on making your assumptions and failure modes explicit.

#### `invoices.csv`

The finance team's spreadsheet version of the same invoices.

**Important: this is not ground truth.** It contains errors and gaps relative to the source PDFs. It represents the customer's current noisy state, not the target. You may use it for exploration, weak comparison, or as a candidate list for the bank-matching step in Task 3. **Do not use it as labels for Task 2.** Create the golden labels by inspecting the PDFs manually.

| Column | Type | Notes |
| --- | --- | --- |
| `invoice_id` | string | Supplier invoice number (format varies by supplier) |
| `supplier_name` | string | May contain typos, abbreviations, or alternate names |
| `amount` | string | Mixed formats: `"1,234.56"`, `"1234.56"`, `"DKK 1234.56"`, `"1.234,56"` |
| `currency` | string | DKK, EUR, or USD. Sometimes missing (assume DKK) |
| `invoice_date` | string | Mixed formats: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY` |
| `due_date` | string | Same mixed formats. Occasionally missing |
| `po_reference` | string | May be blank or in varying formats |

#### `bank_transactions.csv`

Outgoing payments for the same period. Used in Task 3.

| Column | Type | Notes |
| --- | --- | --- |
| `txn_id` | string | Bank-generated transaction ID |
| `date` | string | `YYYY-MM-DD` |
| `amount` | number | Always DKK, negative for outgoing |
| `counterparty` | string | Bank's record of the payee, often truncated |
| `reference` | string | Free text. May contain invoice / PO numbers |
| `category` | string | Bank category |

### Known Data Issues

- Supplier names differ between PDF, CSV, and bank records
- Some PDFs are in Danish or German; at least one is scanned/image-only
- Some invoices were paid in a single combined bank transaction (batch payments)
- Two invoices have a 2% early-payment discount applied in the bank transaction but not on the invoice
- One EUR invoice has been paid in DKK (FX conversion)
- Some bank transactions are not supplier payments (transfers, fees, payroll, tax)
- At least one PDF has values that could plausibly be confused (subtotal vs total vs VAT)
- The CSV has known errors when compared to the source PDFs

---

## Tasks

Complete the following in your preferred language. Python is the natural choice given the LLM ecosystem; Go and TypeScript are also fine. Use whatever you are most productive in.

### Task 1: LLM-based Invoice Extraction

Build an extraction step that reads supplier invoice PDFs and produces a normalised structured representation. Define the schema yourself. At minimum: `invoice_id`, `supplier_name`, `amount`, `currency`, `invoice_date`, `due_date`, `po_reference`. Line items are optional.

You may use any approach: LLM over extracted text, multimodal/vision model, OCR + regex, or a hybrid. Document the choice and the tradeoffs.

Requirements:

- Structured output per invoice (you define and validate the schema)
- A per-field confidence signal. See note on confidence below
- Cleanly surface "unknown" rather than inventing values to satisfy the schema
- One bad PDF must not take down the run. Failed documents should produce an **error record** in the output (with reason and what was attempted) rather than crashing the pipeline

**On confidence.** Do not assume model-reported confidence (`"confidence": 0.93` in the JSON) is calibrated. It usually isn't. If you use it, explain how you would validate or calibrate it. Derived confidence is often better: agreement across runs or models, schema and validation checks, regex on canonical formats, downstream consistency. Tell us what you chose and why.

### Task 2: Evaluation Harness

Pick 5–6 PDFs and create a small labelled golden dataset. We want to see how you decide what "correct" means for each field. **Do not derive labels from `invoices.csv`.** You may compare against the CSV as an additional diagnostic, but it cannot serve as your source of truth.

Build a harness that:

- Runs your extractor over the golden set
- Reports per-field accuracy with metrics appropriate to each field (exact match where it makes sense, fuzzy or semantic comparison where it doesn't, your call, justify it)
- Distinguishes **hallucinations** (extractor returned a confident wrong value) from **misses** (extractor admitted unknown)
- Outputs a regression-style report you could run in CI

Document why string equality is insufficient for some fields and what you replaced it with. We are aware that 5–6 examples is a tiny sample. We care more about your evaluation design than the headline number.

### Task 3: Confidence-based Triage

Wire your extractor and a simple matcher (against `bank_transactions.csv`) into a small end-to-end pipeline. **The matcher can be trivial: fuzzy supplier name plus amount within tolerance is enough.** Spend the time on the routing decision rather than on building a better matcher.

For each invoice, classify the outcome as:

- **Auto-accept**: high confidence end-to-end
- **Review**: extraction or match confidence below threshold (include the reason)
- **Reject**: data-integrity failure (explain)

Pick the thresholds yourself. Use your eval harness to motivate them, and be explicit about the limitations of calibrating from such a small sample. We care more about your calibration approach than the specific values.

### Task 4: Design Discussion (Written)

In a short document (1–2 pages):

1. **Production rollout.** How would you take this from a script to a production agentic pipeline running daily across many tenants? What's the orchestration layer, what's queued, what's synchronous, what is interruptible for human review?
2. **Catching regressions.** A new model version drops and accuracy on the golden set moves: some fields improve by 2%, others regress by 4%. How do you decide whether to ship it? How do you instrument the production system so you would notice this in a live tenant before a customer complains?
3. **Cost and latency.** Walk through three model-selection levers you would actually use here (e.g. cheaper-first cascade, vision vs text-only, batching). Give order-of-magnitude estimates for when each lever is worth pulling.
4. **Drawing the line.** Some parts of this pipeline belong to deterministic code, some to LLMs, some to humans. Where are those boundaries in your design, and why? Name at least one place where you would resist using an LLM even though it could technically work.

---

## Stretch Challenges (Optional)

Pick one if you finish core tasks with time to spare. None of these are required.

- **A. Self-correction loop.** When extraction confidence is low for a field, have the system re-prompt with a more targeted strategy (different model, narrower input, schema-constrained output). Show a measured improvement on the eval set.
- **B. Observability.** Add structured tracing so each LLM call (inputs, outputs, latency, tokens, model version) is logged in a format you'd actually debug a production bug with. Show a sample trace and describe what you would alert on.
- **C. Evidence spans.** Have the extractor return, alongside each value, the page and text span the value came from. Show one case where this would catch a hallucination that confidence alone wouldn't.

---

## Deliverables

**Minimum acceptable submission:**

- Runnable extraction script
- Golden labels for 5 PDFs + eval harness output
- A short design note (can be much less than 2 pages)
- README with setup and how to run

Triage (Task 3) can be partial if you document what remains. We would rather see Tasks 1–2 done thoughtfully than all four tasks rushed.

**Full submission also includes:**

- End-to-end triage pipeline output (Task 3)
- Full design discussion (Task 4)
- Any stretch work

Submit as a Git repo (GitHub, GitLab, or zip). We will provide Anthropic or OpenAI API credentials on request if you'd rather not use your own.

### What This Is Not

We are not looking for production-grade error handling, 100% accuracy, or a polished UI. Incomplete but thoughtful work tells us more than a rushed complete solution.

---

## Logistics

- **Time limit:** Please spend no more than 4 hours on the core tasks (Tasks 1–4). Stretch work is extra.
- **API credentials:** We can provide Anthropic / OpenAI / Vertex credentials on request.
- **Questions:** If anything is unclear, reach out. Asking good questions is part of the role.
- **AI tools:** You may use AI assistants. The follow-up conversation is central. Be ready to walk us through:
  - one extraction failure and why it happened
  - one golden-set label you found ambiguous
  - one threshold choice and how you arrived at it
  - one place you deliberately avoided using an LLM
  - one production metric you would alert on
