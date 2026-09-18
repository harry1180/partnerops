# Cloud PartnerOps — Architecture Overview

Status: Phase 0 (foundation) implemented; Phases 1–6 planned. See
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for verified state.

## 1. System context

```
                         ┌──────────────────────────────────────────┐
  Partner staff          │              Next.js web app             │
  (console)      ──────► │  console + customer portal + white-label │
  Customer staff         └──────────────┬───────────────────────────┘
  (portal)                               │ /api-backend (same-origin
                                         ▼  rewrite w/ cookies+CSRF)
                         ┌──────────────────────────────────────────┐
                         │           FastAPI API (apps/api)         │
                         │  auth · RBAC · RLS scope · billing engine │
                         │  ingestion · invoices · recon · audit     │
                         └───────┬───────────────┬──────────────────┘
                                 │               │
                    ┌────────────▼───┐    ┌──────▼─────────────┐
                    │  PostgreSQL 16 │    │  Redis 7 (queue +  │
                    │  + row-level   │    │  cache + rate      │
                    │  security      │    │  limiting)         │
                    └───────▲────────┘    └──────┬─────────────┘
                            │ migrate/app roles   │ Celery broker
                    ┌───────┴────────┐    ┌──────▼─────────────┐
                    │  Alembic (owns │    │  Workers (/workers │
                    │  schema)       │    │  celery -A)        │
                    └────────────────┘    └──────┬─────────────┘
                                                 │
                                   ┌─────────────▼──────────────┐
                                   │ S3-compatible object store │
                                   │ (MinIO local / S3 prod)    │
                                   │ raw billing files · exports│
                                   └────────────────────────────┘

  Optional later (NOT required locally): AWS CUR S3 buckets, Azure
  Cost Management exports, ERP/ticketing/chat integrations, OIDC/SAML IdPs.
```

## 2. The central pipeline

```
raw file (SHA-256, stored)
  → RawBillingFile / RawBillingRecord (verbatim payload + dedupe key)
  → CanonicalCostRecord (provider-neutral, NUMERIC money, lineage to file+row)
  → allocation (CloudAccount → AccountFamily → Customer; unmapped => DQ exception)
  → ContractVersion (pinned) × BillingRuleVersions (pinned, ordered by
    priority/calc_order) × engine version
  → PricingRun + PricingRunItem (per-line lineage: source ids, rule ids,
    formula, input/output, actor, approval state) + rule snapshots
  → Invoice (grouped) → lifecycle → PDF/CSV → customer portal
  → ReconciliationRun (provider bill ↔ canonical ↔ invoice totals)
  → exceptions/waivers → period close
  → every step audited in audit_events (append-only, DB-enforced)
```

Determinism: same inputs (file checksum + contract version + rule versions +
engine version) ⇒ byte-identical amounts. Money is `Decimal`/`NUMERIC(20,6)`
end-to-end; floats are rejected at the API boundary and by a unit test.

## 3. Monorepo

| Path | Contents |
|------|----------|
| `apps/api` | FastAPI app: models, services, `engine/` (billing), `ingestion/` (adapters), Celery tasks, Alembic, seed |
| `apps/web` | Next.js App Router: `/login`, `/(console)/*` partner console, `/(portal)/*` customer portal (Phase 1), ECharts (Phase 1) |
| `workers` | Celery worker entrypoint |
| `packages/shared` | TS domain types + money display helpers (BigInt cents, no float math) |
| `packages/ui` | Original accessible component kit + design tokens (brand via CSS vars) |
| `infra` | Docker Compose, Dockerfiles, Postgres role bootstrap; production templates later |
| `docs` | This overview, ADRs (`docs/decisions/`), ERD, runbooks, guides |
| `fixtures` | Deterministic synthetic billing data (Phase 1) |
| `tests/e2e` | Playwright journeys (Phase 1) |

## 4. Multi-tenancy & isolation

* **Org tree**: platform → distributor → reseller/MSP → customer (+ business
  units under customers later). `organizations.path` is a materialized path of
  org UUIDs. Every tenant-scoped table carries `org_path`.
* **Database enforcement**: PostgreSQL RLS on all tenant tables. Predicate:
  `left(org_path, length(current_setting('app.current_org_path', true))) =
  current_setting(...)`. The GUC is set per transaction by the request
  dependency to the caller's subtree root. Unset/`@@deny@@` ⇒ zero rows.
  The app connects as role `partnerops` (NOBYPASSRLS); the owner role bypasses
  RLS and is used only by migrations/tests-setup. Verified by
  `tests/test_pg_isolation.py` (real Postgres, runs in the docker stack).
* **Application enforcement**: `require(permission)` gates + org-scope checks
  on every mutation; cross-tenant objects answer **404** (no existence
  leakage). RBAC roles → permission sets (`services/authz.py` is the single
  catalog); user↔role↔org assignments are the ABAC-ready seam.
* **Secrets & PII**: passwords argon2id; API tokens stored hashed; audit
  `detail` passes through `redact()`; demo seed refuses non-local envs.

## 5. AuthN / AuthZ

Local cookie sessions (HttpOnly SameSite=Lax; CSRF double-submit for unsafe
methods) behind the `IdentityProvider` protocol; OIDC (auth-code callback) and
SAML (ACS route) are additive adapters later. API tokens: `Authorization:
Bearer cpo_…` with scopes that intersect (never widen) the creator's
permissions; rotation supported. MFA field/flow-ready (`mfa_secret_encrypted`,
enrollment gated at login when enabled).

## 6. Billing engine (apps/api/app/engine)

* Pure, versioned (`CALC_ENGINE_VERSION`), no DB access — the pricing service
  feeds it records + pinned rule versions and writes `PricingRunItem` lineage.
* Rule kinds: markup/discount %, fixed recurring/one-time, unit rate, tiered,
  min/cap, MSF, support, credit pass-through/retention, SKU override,
  marketplace, tax, currency, exclusion, promo credit, manual adjustment.
* Filters match canonical records (customer/family/account/service/SKU/region/
  resource/usage type/tag/app/env/owner/cost-center/period).
* Order: priority (precedence) then calc_order (sequence) then deterministic
  tie-break; `applied_basis` = running_total or original_input.
* Residuals (min-charge top-ups, caps) are emitted as explicit line kinds —
  never silent fudging. Rounding: half_up default, per-line/per-invoice
  configurable on ContractVersion.

## 7. Ingestion framework

Adapter interface (`ingestion/base.py`): discover → fetch manifest → parse
(streaming) → map to canonical → dedupe (sha256 of semantic identity) →
quarantine invalid rows → upsert `ProviderBillTotal`. Idempotent by
`(sha256, parser_version)` file uniqueness; reprocessing writes a NEW pricing
run and links `supersedes_id`. AWS CUR v1 (CSV/Parquet later) Phase 1; Azure
cost-export v1 shipped Phase 3 (synthetic format, same contract; live fetch =
Phase 5 credentials integration, docs/cloud-connectors.md); GCP = interface only.

## 8. Performance path

NUMERIC + partitioned `canonical_cost_records` by billing period (Phase 6
pg_partman-style manual range partition; model ready), materialized daily
rollups for dashboards (Phase 1), server-side pagination everywhere, chunked
CSV parsing, Celery queues `pricing`/`ingestion`/`default`, cached aggregates
in Redis, object-storage streaming. Documented targets: 1,000 customers ·
25,000 accounts · 100M cost records/month — achieved via partition + rollup +
job fan-out; no premature distributed infra (ADR 0009).

## 9. Observability

structlog JSON logs + correlation ID middleware (echoed in `X-Request-ID`),
Prometheus `/metrics`, `/health/live` + `/health/ready` (db/redis/object
store), OpenTelemetry-ready (instrumentation seam documented in ADR 0006).
Operational runbook: `docs/runbook.md`.
