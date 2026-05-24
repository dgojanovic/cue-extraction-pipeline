# Production Design

## Architecture

Move the script into a durable multi-tenant workflow: ingest document, store source file, extract/OCR, run LLM extraction, validate deterministically, reconcile against bank data, then route to `auto_accept`, `needs_review`, or `reject`.

Use a durable workflow engine plus queues. The workflow engine tracks document state, retries, and human-review pauses. Queues execute OCR, LLM extraction, validation, triage, reporting, and notification workers.

Support both ingestion modes:

- scheduled tenant batches for daily exports or polling SFTP, inboxes, or storage buckets
- event-driven ingestion for uploads, email processors, webhooks, or object-storage events

Both modes create the same tenant-scoped `batch` and `document` records, then enter the same document workflow.

The platform owns authentication, ingestion, scheduling, queues, storage, database records, rate limits, audit persistence, observability, and retention. The workflow layer owns document state transitions. For many tenants, enforce per-tenant queue quotas, concurrency limits, and priority controls so one tenant's batch cannot starve others or block interactive review work.

## Sync vs Async

Synchronous:

- authenticate tenant/user
- accept upload or ingestion event
- store original file
- create ids and return status
- accept reviewer actions

Asynchronous:

- OCR
- LLM extraction
- candidate validation
- bank reconciliation
- report generation
- notifications

Slow, expensive, retryable, or rate-limited work should not block the request path. Failed jobs should retry with backoff, then produce dead-letter/error records.

## Human Review

Human review is an interruptible workflow state after triage and before final approval, payment action, accounting export, or any irreversible downstream step.

Route to `needs_review` when extraction confidence is low, critical fields are missing, validation fails, bank matching is questionable, amounts mismatch, multiple matches exist, or tenant policy requires approval.

Reviewer actions are explicit events: approve, reject, correct fields, or request reprocessing. After correction, resume from validation or triage instead of restarting the full document flow.

## Agentic Extension

The agentic layer should be modular and versioned. Today it extracts invoice fields; later it may add classification, anomaly explanation, duplicate detection, policy reasoning, reviewer-assist summaries, or follow-up question generation.

Each agentic step should record provider, model, prompt version, inputs, outputs, latency, cost, and validation result. Models/providers should be swappable per step without rewriting the full pipeline.

Evaluation and observability should be first-class: golden-set regression tests, per-field accuracy, hallucination rate, missing-field rate, review rate, auto-accept rate, model cost, and latency by tenant, document type, model, provider, and prompt version.

## Deterministic, LLM, Human

LLMs handle semantically messy inputs:

- invoice field extraction
- scanned/layout-heavy documents
- supplier and line-item interpretation
- reviewer-assist summaries

Deterministic code handles repeatable policy:

- schema validation
- date/currency/amount normalization
- total calculations
- candidate matching
- bank reconciliation checks
- threshold decisions
- tenant rules
- idempotency and duplicate detection
- final auto-accept/reject routing

Humans handle ambiguity and accountability:

- correcting extracted fields
- approving questionable matches
- resolving amount mismatches
- handling multiple plausible matches
- approving model tradeoffs when business-critical fields regress

I would resist using an LLM for final payment approval or bank reconciliation policy. It could technically compare invoice and bank data, but final routing should be deterministic: invoice id checks, amount tolerance, currency match, validation status, and tenant rules. That keeps decisions reproducible and auditable.

## Model Rollouts

Do not ship model upgrades on aggregate accuracy alone. Gate by field-level changes and business impact.

Ship only if:

- critical fields such as invoice id, currency, totals, tax, and payment match do not regress beyond agreed tolerance
- hallucination and missing-field rates stay within limits
- review and auto-accept rates do not move unexpectedly
- cost and latency remain acceptable
- tenant-specific canaries stay within live thresholds

If a model improves some fields but regresses others, avoid forcing one global choice. Split important fields into separate agentic steps or use different prompts/models/providers when the business value justifies added latency, cost, and evaluation complexity.

Use shadow runs, canaries, and tenant-scoped feature flags. Alert on validation-failure spikes, reviewer correction rate, override rate, review-rate increases, auto-accept drops, hallucination increases, latency/cost spikes, and tenant/document-type drift.

## Model Selection Levers

| Lever | Use When | Order-of-Magnitude Impact |
| --- | --- | --- |
| Cheaper-first cascade | `70-80%` of documents pass a cheaper model cleanly; fallback rate stays below `30-40%`. | Often `50%+` model-cost reduction if fallback stays low. |
| Text-only vs vision | Embedded/OCR text validates critical fields; use vision for scanned, poor-OCR, layout-heavy, or table-heavy invoices. Worth it when these are `5-10%+` of volume or drive many reviews. | Lower cost/latency for digital PDFs; better accuracy on targeted hard cases. |
| Batch processing | Latency tolerance is hours, or volume is hundreds/thousands of documents: daily runs, backfills, evals, shadow runs. | Around `50%` model-processing cost reduction when batch latency is acceptable; very large datasets can also improve throughput via async batch capacity. |

Keep routing tenant-, document-type-, and risk-aware. Add complexity only when evals or production metrics show the cost/quality/latency tradeoff is worth it.

## Storage and Ops

Store original and generated artifacts: source invoices, extracted text, OCR text, extraction outputs, validation outputs, triage reports, evaluation reports, and error records.

Every stored object should include `tenant_id` and parent ids. Exact file metadata depends on wider platform requirements; a baseline is `file_id`, `batch_id`, `document_id`, `kind`, `storage_uri`, `mime_type`, `size_bytes`, `sha256`, and `created_at`.

Required production controls: tenant isolation, idempotency by tenant/source/checksum, retries with backoff, dead-letter records, model/prompt/policy versioning, per-tenant configuration, metrics, encrypted storage, access control, and retention policies.
