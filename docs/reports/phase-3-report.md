# Phase 3 report — Azure and multi-cloud ingestion

Date: 2026-09-17 · HEAD: `phase-3` commit (after 794d289)

## Completed functionality

| Area | What shipped |
|------|--------------|
| Azure adapter | `SyntheticAzureAdapter` (parser v1) behind the same `BillingFileAdapter` contract as AWS; clean-room interchange format (no provider schema copied). Field map in docs/cloud-connectors.md. |
| Azure demo dataset | Deterministic fixtures (3 months, seed-pinned): 3 subscriptions + EA enrollment, resource groups, reservation-covered lines (billed < on-demand), monetary-commitment credit + refund, planted duplicate (July), orphan subscription, +$180 true-up (August provider_summary), late June adjustment (August file). |
| Subscriptions & resource groups | Seeded as first-class rows for Northwind customers (Acme, BlueRiver) — the same customers now carry AWS accounts *and* Azure subscriptions (multi-cloud per customer). |
| Provider bill totals per account | `provider_bill_totals.level` (invoice|account): enrollment statement + per-cloud-account rollup computed at ingest, each carrying file/sha evidence. New endpoint `/reconciliation/bill-totals`. Reconciliation leg A reads invoice-level only — no double count. |
| Reconciliation | Multi-cloud verified live: June/August recon surfaces the merged provider-vs-canonical delta (AWS +4500 ⊕ Azure +180) and the Azure unmapped-usage exception under the payer ref. |
| Multi-cloud pricing & lineage | Pricing runs aggregate both providers per customer; invoice line lineage resolves to source ids from both fixtures via `/invoices/{id}/lineage`. |
| Connector scheduling | `provider_connectors` model/migration (RLS-enabled), API (`/connectors` CRUD-lite + run/toggle, RBAC on `integration.manage`), Celery beat `run_due_connectors` (15 min, bypass-discovery + per-org scope), audited `integration.connector_created/updated/ran`. Synthetic mode only; `fetch_available=false` reported honestly everywhere (ADR-0016). |
| Azure upload endpoint | `POST /ingestion/azure/upload` (shared upload body with AWS; correct source attribution `azure_manual_upload`). |
| UI | Cloud Accounts page: Azure import button alongside AWS; connector panel (cadence, last ingest, next due, Run now/Toggle, "no live pull" label). Nav honesty kept: no dead Integrations page — that ships Phase 5; `CURRENT_PHASE=3`. |
| Docs | ADR-0016 (synthetic connectors + level grain), docs/cloud-connectors.md, ERD Phase 3 section. |

## Tests & verification (all re-run 2026-09-17)

- `ruff check app` clean · `mypy app` clean (81 files) · `pytest` **100 passed**
  (6 RLS included), new suites: `test_azure_ingestion.py` (13: mapping,
  determinism, quarantine, enrollment-scope, +2 hypothesis fuzz), 
  `test_phase3_connectors.py` (6: lifecycle, evidence, due-pass, RBAC, API flow)
- Hypothesis fuzz invariants: adapter never crashes on malformed CSV;
  `drafts + issues == row_count` (no silent drops); Decimal round-trip holds
  over random 6-dp amounts.
- PostgreSQL: fresh `DROP SCHEMA` → `alembic upgrade head` (new revision
  `c9a4d1e7f302` incl. level-constraint migration) → seed 11 orgs/9 users;
  RLS + 1 policy verified live on `provider_connectors`; uq index includes
  `level`.
- Live smokes: `smoke_phase1_live.py` **SMOKE_OK** (25 steps) ·
  `smoke_phase2_live.py` **SMOKE2_OK** (17 steps) · **NEW**
  `smoke_phase3_live.py` **SMOKE3_OK** (22 steps: connector run → DQ →
  bill-totals grain → merged recon deltas → multi-cloud pricing → lineage →
  audit).
- Playwright journey: **14 passed, 1 skipped** (assistant=Phase 5) — new step
  **2b** imports Azure via UI, asserts the orphan subscription row and the
  honest connectors panel; passed twice consecutively.
- Web: `typecheck` 0 errors · vitest 8 · `next build` clean.

## Bugs found & fixed this phase

1. **Per-account rollup collided with invoice-level uniqueness** — Azure
   enrollment-scope rows land under the same ref as the EA statement; on
   sqlite (4-col constraint retained) the insert died with IntegrityError.
   Fixed by attributing enrollment-scope rollups to the invoice-level row:
   at most one `provider_bill_totals` row per (provider, ref, period, level)
   on every dialect (rule written into ADR-0016).
2. **`?` unmapped noise from enrollment rows** — the "unmapped" worklist is
   for lost accounts, not the EA itself; enrollment lines now roll up under
   the payer account, and only truly-unmapped account ids reach DQ.
3. **naive-datetime comparisons on sqlite** — connector cadence fields read
   back naive-UTC (known Phase 2 trap); `_aware()` normalizes in the service,
   tests assert with the same normalization.
4. **Scheduled-run audit FK** — worker-side audit events now use
   `principal=None, actor_kind="system"` (Phase 2 pattern) instead of a
   synthetic user id with no users row.

## Known limitations at this gate (honest list)

1. No live cloud fetching anywhere — synthetic/manual-upload modes only;
   credentials, S3/Cost-Management polling, and webhook fan-out are Phase 5
   integration work on this seam.
2. GCP remains interface + planned row only.
3. Azure cost filters, currency conversion for multi-currency exports, and
   amortization of reservation purchases beyond the export's own amortized
   column are later-phase scope.
4. Connector edit UI (cadence change) is API-only; the panel exposes
   Run/Toggle, which is all that works end-to-end today.

## Next: Phase 4 — FinOps & governance

Budgets, forecasts, anomaly detection (statistical, honest thresholds),
rightsizing/idle-resource recommendations, tag compliance, governance policy
engine with findings, exceptions, remediation guidance.
