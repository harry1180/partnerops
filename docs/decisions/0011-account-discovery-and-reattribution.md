# ADR-0011: Account auto-discovery and re-attribution on mapping

Date: 2026-09-16
Status: Accepted

## Context

Phase 1's first ingestion implementation only resolved linked accounts that
already existed as `CloudAccount` rows (created by the seed). Accounts that
appeared in provider billing but had never been provisioned produced canonical
cost rows with `customer_id = NULL` and no `cloud_account_id` — the data
existed but the allocation workflow had nothing to show, so "which accounts
are missing from invoicing?" was unanswerable from the UI. The Playwright
journey (map an unmapped account in the UI) exposed this: the worklist page
found nothing to map.

## Decision

1. **Auto-discovery.** During ingestion, any `linked_account` value not found
   in the org's account index is created as a `CloudAccount` with
   `allocation_status = 'unmapped'` (payer accounts are labeled `payer`).
   Discovery is idempotent: the in-file index is updated so one file with N
   rows for a new account creates one row.
2. **Unmapped semantics.** An account row with no `account_family_id` counts
   as unmapped even though the row exists; ingestion reports it in
   `unmapped_accounts` exactly as before, so data-quality dashboards keep
   working.
3. **Re-attribution on mapping.** `PATCH /cloud-accounts/{id}` updates the
   account AND issues a scoped `UPDATE canonical_cost_records SET
   customer_id, account_family_id, org_path, org_id` for the account's rows,
   inside the caller's RLS scope. Mapping is audited (`cloud_account.mapped`).
4. **Payer guard.** AWS payer accounts are rejected as bill-ees
   (`payer_not_billee`) — the payer consolidates; linked accounts are the
   billing grain.

## Consequences

- Pricing reads current attribution at run time, so mapping → pricing works
  without re-ingesting a period (re-ingestion remains idempotent and is still
  the recovery path if raw data changed).
- Re-attribution moves rows across RLS paths; the UPDATE runs with the
  caller's scope bound, and the new `org_path` is always a descendant of the
  caller root (family rows are loaded through the scoped session first, so a
  cross-tenant family id cannot be referenced — it 400s with
  `family_required`).
- Excluding an account reverses attribution to the partner org path with
  `customer_id = NULL`, which the reconciliation `unmapped_account` exception
  then surfaces — exclusion is never silent.
- The discovered row's `display_name` is a placeholder ("Discovered <id>");
  enrichment from AWS Organizations is a Phase 2+ connector concern.
