# 0008 — Idempotent, restartable ingestion with quarantine

Date: 2026-09-16 · Status: Accepted (Phase 1 implements adapters)

## Context
Provider billing files are re-delivered, re-parsed after bug fixes, and
sometimes corrected late. Invoicing must never double-charge.

## Decision
- Every raw file lands in object storage keyed by SHA-256; `raw_billing_files`
  is unique on `(sha256, parser_version)` ⇒ re-submitting the same bytes is a
  no-op, while a new parser version re-parses without duplicate rows.
- Row identity: `dedupe_key` = sha256 of provider-semantic fields
  (invoice id, line item id, usage window, account, sku, operation, type)
  → exact duplicates collapse; near-duplicates surface as data-quality
  exceptions rather than silent loss.
- `canonical_cost_records` is unique on `(source_record_id, lineage_file_id)`
  and carries row-level lineage; raw payloads stay in `raw_billing_records`.
- Invalid rows (bad currency, unparseable dates, missing account) go to
  `quarantined_records` with a reason — never dropped.
- Late-arriving data is flagged (`is_late_adjustment`) against the closed
  period and routed into reconciliation exceptions.
- Reprocessing a period runs a NEW `pricing_runs` row (`run_number` +
  `supersedes_id`) — historical runs and issued invoices remain untouched;
  corrections flow through credit/debit notes, not deletes.
- Files and records retain `correlation_id` and `parser_version` so any
  number is traceable to "which bytes, which parser, which run".

## Consequences
- Storage cost for raw files is a feature (audit), not waste.
- Adapters must keep `dedupe_key` stable across parser improvements or a
  version bump is required.
