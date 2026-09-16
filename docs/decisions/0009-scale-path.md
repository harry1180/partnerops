# 0009 — Scale by partition, rollup and background jobs — not distributed infra

Date: 2026-09-16 · Status: Accepted

## Context
Targets: 1,000 customers · 25,000 accounts · 100M cost records/month ·
concurrent pricing/reporting jobs — while staying a single compose-friendly
stack today.

## Decision
- PostgreSQL remains the system of record. `canonical_cost_records` (and
  raw/lineage tables) partition by `billing_period` range (PG16 native;
  Phase 1 ships the declarative partition migration for new periods,
  backfill optional). All hot queries carry `billing_period_start` filters —
  partition pruning is automatic.
- Dashboards never scan raw rows: daily/monthly rollup tables
  (`usage_rollups_*`) populated during ingestion + a scheduled refresh
  (Phase 1 for provider cost, Phase 4 for FinOps). Redis caches dashboard
  aggregates keyed by (scope, filters, period) with versioned invalidation.
- Heavy work leaves the request path: Celery queues `pricing` / `ingestion`
  / `default`; pricing runs are jobs with progress + idempotency, not inline
  requests. Multiple runs execute concurrently per tenant with row-level
  locking per (customer, period).
- Ingestion streams/parses in chunks (CSV rows, Parquet row groups later);
  exports stream from query cursors to object storage.
- API is stateless (sessions in DB) → horizontal scale behind a load
  balancer; Postgres read replicas + PgBouncer are the first ceiling move;
  ClickHouse is a documented *option* at >500M rows/month, not introduced
  now (premature distribution is its own failure mode).

## Consequences
- A clear, tested path to scale with ops kept boring; rollup correctness
  tests become part of Phase 1 golden fixtures.
