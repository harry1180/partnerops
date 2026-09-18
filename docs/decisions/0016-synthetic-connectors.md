# ADR-0016: Synthetic-mode connectors, honest fetch boundaries

Date: 2026-09-17
Status: accepted
Phase: 3

## Context

Phase 3 adds multi-cloud ingestion (Azure behind the same provider-neutral
ingestion contract). The charter requires connector *scheduling* and
*integrations* while also requiring (a) zero cloud credentials for local dev
and (b) "no fake buttons" — a control must do real work or be labeled.

A naïve "Integrations" page with a Pull-now button calling nothing would
violate (b); refusing to build any connector surface would leave scheduling
untestable and Phase 5 without a seam.

## Decision

1. `ProviderConnector` rows are configuration + evidence, not magic:
   mode is `synthetic` (the only implemented mode), and every status field
   (`last_ingest_at`, `last_file_id`, `next_due_at`, `due_now`) is written
   ONLY by a real ingestion of a real file (sha256-backed
   `raw_billing_files` row). The API always reports
   `fetch_available: false`; the UI renders "(no live pull)" instead of a
   dead button.
2. `run now` and the Celery beat pass both ingest the deterministic fixture
   through the same `ingest_csv` service path as a manual upload — one code
   path, no demo-only shortcut. Idempotency comes from the file sha256, so
   re-runs report `skipped_duplicate_file` honestly.
3. `provider_bill_totals` gains a `level` column: `invoice` (the enrollment/
   payer statement — reconciliation leg A) and `account` (per-cloud-account
   rollup computed at ingestion time). Reconciliation reads level=invoice
   only, so the new grain can never double-count. The unique key is
   (org_path, provider, ref, period_start, level); a ref that is BOTH the
   billing account and an account (Azure enrollment-scope rows) is
   attributed to its invoice-level row, keeping at most one row per
   (provider, ref, period) — a rule that holds on sqlite (where the 4-col
   unique constraint lives) and Postgres alike.
4. Azure enrollment-scope lines (tax/credits with subscription == billing
   profile) are real bill rows with no subscription: the adapter emits
   `linked_account=None` (payer leg preserved) rather than inventing an
   account or quarantining a valid row. Ingestion attributes them to the
   partner org path, and they surface as the (correct) "unmapped usage under
   the payer account" reconciliation exception rather than vanishing.

## Consequences

- The whole Phase 3 scheduling story is testable end-to-end today; swapping
  synthetic mode for a real S3/Cost-Management fetch later changes only the
  ingest *source*, never the accounting.
- Live connector credentials, OIDC-role assumptions, and webhook fan-out
  remain Phase 5 integration work with this as their seam.
