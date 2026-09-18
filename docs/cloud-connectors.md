# Cloud Connector Guide

How provider billing data gets into Cloud PartnerOps, and what is real today
versus what is a planned seam. Every claim here is enforced by code paths that
either run or fail loudly — the product ships no fake fetch buttons (ADR-0016).

## The ingestion contract

Everything flows through one service entry point:

```
upload bytes → raw_billing_files (sha256, parser_version, status)
            → adapter.parse(text, IngestContext) → CanonicalDraft stream
            → raw_billing_records (verbatim JSON per row, lineage)
            → canonical_cost_records (provider-neutral, Decimal amounts)
            → provider_bill_totals (invoice-level + per-account rollups)
```

Invariants enforced by the service, not the adapters:

- **Idempotent**: re-submitting identical bytes with the same
  `parser_version` returns `skipped_duplicate_file` — never a double ingest.
- **Dedupe**: semantic `dedupe_key` collisions quarantine the second row
  (`duplicate_record`) instead of double-charging.
- **Quarantine**: any row failing validation lands in `quarantined_records`
  with a reason code; nothing is silently dropped (fuzz-tested invariant:
  drafts + issues == row_count).
- **Auto-discovery**: accounts seen in provider billing with no console row
  become unmapped `CloudAccount`s — they surface on the allocation worklist
  and the data-quality dashboard.

## AWS (`aws`, parser v1)

`SyntheticAwsAdapter` parses our clean-room CUR-shaped interchange
(`fixtures/aws/<payer>/<period>.csv`, generator: `app/ingestion/synthetic_aws.py`).
A real CUR-on-S3 adapter is a format swap behind the same contract; live
credentials are a Phase 5 integration.

## Azure (`azure`, parser v1) — Phase 3

`SyntheticAzureAdapter` parses our clean-room cost-export shape
(`fixtures/azure/<billing-profile>/<period>.csv`, generator:
`app/ingestion/synthetic_azure.py`). Mapping:

| Azure fixture field | Canonical field |
|---|---|
| billing_profile_id | payer_or_billing_account (EA enrollment leg) |
| subscription_id | linked account (CloudAccount, provider azure) |
| subscription == profile | enrollment-scope line → linked account None (tax/credits live at the EA) |
| resource_group, reservation_id, unit_price | source_metadata (kept for lineage + commitment analytics) |
| meter | sku / usage type |
| pre_tax_cost | provider billed |

The seeded demo includes Azure subscriptions under two Northwind customers
(Acme, BlueRiver) so the same customer's pricing run mixes AWS + Azure usage;
one orphan subscription (`unmapped-0000-…`) exercises discovery; the August
export carries a planted +$180.00 true-up that reconciliation must surface.

## Connectors (scheduling)

`provider_connectors` rows bind a partner org to a provider billing account
with a cadence (monthly day-of-month at hour UTC). The Celery beat task
`app.tasks.connectors.run_due_connectors` (every 15 min) ingests due
connectors under each connector's own org scope, records
`last_ingest_at`/`last_file_id`, advances `next_due_at`, and writes
`integration.connector_ran` audit events.

Modes today: `synthetic` only. The API reports `fetch_available: false` and
the UI renders "(no live pull)" — a connector "Run now" ingests the real
fixture file through the same `ingest_csv` path as a manual upload.

Live connectors (AWS CUR on S3, Azure Cost Management exports via OIDC/role)
replace the source, never the accounting: see Phase 5 integrations.

## GCP

Adapter interface only (`cloud_providers` row `gcp_billing`, status
`planned`). No implementation is claimed or faked.
