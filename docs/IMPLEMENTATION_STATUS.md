# Implementation Status

Working source of truth for what is verified. Updated at every phase gate.
Last update: Phase 1 — **complete** (journey 13 passed / 1 skipped-by-design; see docs/reports/phase-1-report.md).

## Phase 0 — Foundation ✅ COMPLETE

Verified by execution (not aspiration):

| Area | Status | Evidence |
|------|--------|----------|
| Monorepo scaffold (web/api/workers/packages/infra/docs) | ✅ | `pnpm install` clean, workspace scripts wired |
| Docker Compose stack | ✅ | postgres/redis/minio `Up (healthy)`; api+worker services defined |
| Initial migration: 51+ tables | ✅ | `alembic upgrade head` applied to real PG 16 (re-verified after DB reset) |
| PostgreSQL RLS on tenant tables + organizations | ✅ | `tests/test_pg_isolation.py` — 6 passing against live PG (deny-all, cross-tenant invisibility, WITH-CHECK write reject, child subtree) |
| Audit trail append-only (grants + trigger + maintenance gate) | ✅ | pg test: UPDATE blocked even for table owner |
| Issued-invoice immutability (trigger) | ✅ | pg test + live HTTP: issued→draft rejected 409 |
| AuthN: argon2id, sessions, CSRF (fail-closed), rate limit, lockout | ✅ | unit/integration tests + live flow |
| RBAC: 8 roles / 50+ permissions / scoped assignments | ✅ | authz matrix tests; customer roles blocked from margin/dq/recon (live HTTP 403s) |
| Org hierarchy: platform → distributor → MSP → customer | ✅ | seed + live API |
| Tenant-scoped APIs + branding + capabilities + phase-gated nav | ✅ | live requests; ADR-0012 |
| Cross-tenant probing answers 404 (no existence leak) | ✅ | tests + journey step 13a |
| Seed framework (idempotent, env-gated) | ✅ | re-run after DB reset: 11 orgs / 9 users / 8 roles |
| Structured logs, correlation IDs, secure headers, /health + /metrics | ✅ | middleware; live checks |
| ADRs 0001–0010 + architecture.md + erd.md | ✅ | docs/ |

## Phase 1 — Working AWS billing MVP ✅ COMPLETE

The full central workflow runs end-to-end with zero cloud credentials:

```
synthetic AWS CUR → normalization → allocation → contracts → rules → pricing
→ invoices (PDF/CSV) → reconciliation → margins → portal → disputes → audit
```

| Area | Status | Evidence |
|------|--------|----------|
| Ingestion framework (idempotent, restartable, dedupe, quarantine, late adjustments, checksums, parser version, raw-file preservation) | ✅ | `test_ingest_service.py`, `test_ingestion_adapters.py`; re-submit returns `skipped_duplicate_file`; provider-summary leg stored |
| Canonical cost model (all charter fields + source metadata) | ✅ | `models/cost.py`; 186 canonical rows/month in demo |
| Account auto-discovery + allocation worklist | ✅ | unmapped accounts surface as CloudAccount rows; map/exclude with audited re-attribution (ADR-0011); UI `/cloud-accounts` |
| Customers, account families, users | ✅ | APIs + UI incl. create flows |
| Versioned contracts (never rewrite used versions; overlap guard) | ✅ | APIs + `/contracts` UI; binding pins published rule versions |
| Billing rules engine: 20 rule types, filters, priority/calc_order, Decimal-only | ✅ | `engine/rules.py`; `test_engine_rules.py`; floats raise TypeError |
| Lineage per calculation (source ids, rule versions, formulas, engine version, actor) | ✅ | `PricingRunItem.calculation_trace`; `GET /invoices/{id}/lineage` |
| Rule Test sandbox (read-only preview + stored evidence) | ✅ | `/billing-rule-versions/{id}/sandbox` + UI modal |
| Pricing runs (reprocess = supersede, history frozen) | ✅ | `test_e2e_pipeline.py`, live smoke |
| Invoices: lifecycle states, immutability, PDF+CSV, numbering, branding | ✅ | live smoke steps 6–7; docs/invoice-lifecycle.md |
| Three-way reconciliation + exception workflow + data-quality dashboard | ✅ | recon run finds the planted unmapped/dup/late cases; `/data-quality`; UI page |
| Margins & leakage (revenue, provider cost, margin, negative/low-margin, unbilled) | ✅ | `/margins/summary|trend` + UI |
| Customer portal (whitelisted projections only; no margin/provider cost/internal notes) | ✅ | `api/v1/portal.py`; payload assertions in smoke + journey |
| Disputes (portal files, partner resolves, customer can't self-resolve) | ✅ | `api/v1/extras.py`; live smoke + journey step 11 |
| Audit trail across the whole workflow | ✅ | 14 distinct actions incl. dispute lifecycle (journey 13b) |
| Backend gates | ✅ | ruff 0, mypy 0 (70 files), pytest 63+6 (6 PG-RLS), golden billing fixtures |
| Frontend gates | ✅ | typecheck 0, vitest 8, `next build` 19 pages |
| Live HTTP workflow rehearsal | ✅ | `tools/smoke_phase1_live.py` — 25 steps SMOKE_OK against :8001 |
| Playwright critical journey (charter 11 steps) | ✅ | `e2e/billing-journey.spec.ts` — **13 passed, 1 skipped** (assistant=Phase 5), 26s, re-runnable via `CPO_TAG`; run twice consecutively |
| Docs | ✅ | billing-engine.md, rule-precedence.md, invoice-lifecycle.md, reconciliation.md, demo-script.md, ADRs 0011–0013 |

### Bugs the journey caught and fixed (this is why it exists)

1. **Tailwind never compiled** (ADR-0013): no PostCSS config; utilities dead;
   build stayed green. Fixed + verified by computed-style probe.
2. **`packages/ui` outside Tailwind's auto-source**: Modal's `fixed inset-0`
   missing → overlay collapsed, buttons unreachable. Fixed with `@source`.
3. **Add-customer modal**: org dropdown never populated (state initialized
   before async fetch). Fixed with sync effect.
4. **Account mapping 500**: re-attribution UPDATE referenced `org_id`, a
   property not a mapped column. Fixed.
5. **Login rate limit (10/min)** tripped by the journey's ~15 logins.
   Raised to 60/min in `.env`/`.env.example` (config remains per-deploy).

### Known limitations at this gate (honest list)

1. Maker-checker approval UI for high-impact rules is Phase 2 (API records
   sandbox evidence; enforcement gate exists as data, not yet as a wall).
2. Credit/debit notes & period-close endpoint: Phase 2 (immutability already
   enforced; corrections currently = new pricing run + new draft invoice).
3. Tiered/minimum/maximum rules are implemented and tested but not yet
   exercised by the demo dataset's contract.
4. Multi-currency: mismatched-currency rows are quarantined, not converted
   (`currency_conversion` rule reserved for Phase 2).
5. Azure adapter: interface + planned provider row only (Phase 3).
6. PDF invoices are generated with a minimal internal writer (no external
   service); rich layout is Phase 2.
7. Windows dev quirks: run API via `run_server.py` (Selector loop); don't run
   `next build` while `next dev` shares `.next`.

## Next: Phase 2 — Advanced billing operations

Tiered demo contracts, commitment/credit allocation policies, maker-checker
UI + enforcement, credit/debit notes, period closing, revenue-leakage alerts,
scheduled reports, full white-label config UI.
