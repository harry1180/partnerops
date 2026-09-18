# Implementation Status

Working source of truth for what is verified. Updated at every phase gate.
Last update: Phase 5 — **complete** (see docs/reports/phase-5-report.md).

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

1. Maker-checker enforcement: shipped in Phase 2 (high-impact rule publish
   requires approval by a different person; waiver path audited).
2. Credit/debit notes & period close: shipped in Phase 2 (issued invoices
   stay immutable; corrections are linked notes).
3. Tiered/minimum/maximum rules are implemented and tested but not yet
   exercised by the demo dataset's contract.
4. Multi-currency: mismatched-currency rows are quarantined, not converted
   (`currency_conversion` rule reserved for a later phase).
5. Azure adapter: shipped in Phase 3 (synthetic clean-room export format;
   live fetch is a Phase 5 credentials integration).
6. PDF invoices are generated with a minimal internal writer (no external
   service); rich layout is a later phase.
7. Windows dev quirks: run API via `run_server.py` (Selector loop); don't run
   `next build` while `next dev` shares `.next`.

## Phase 2 — Advanced billing operations (COMPLETE)

Shipped:

- **Maker-checker approvals**: high-impact rule publishes and period-close
  require approval by a *different* person; waiver path with reason codes,
  fully audited (`approvals.waived`, `approval.requested/approved/denied`).
  UI: Approvals queue.
- **Credit/debit notes** on issued invoices (invoice total recomputed via
  linked note; original stays immutable). UI on invoice detail.
- **Period close**: per-customer close/open with material-exception gate
  (waivable with audited reason).
- **Credits & commitments**: track provider credits/Savings Plans/RIs,
  allocation policies, coverage & utilization computed from canonical cost
  rows. The platform never purchases or modifies provider commitments.
- **Reports**: five on-demand CSV reports (margins, unbilled usage, credits,
  commitment coverage, invoice summary) — same aggregations as the dashboards,
  every download audited (`export.generated`).
- **Scheduled reports**: monthly/weekly cadence, worker-generated CSVs stored
  in object storage with sha256 lineage, `notification_outbox` entries (no
  SMTP in local dev), audit (`report.schedule_ran`), Celery beat every 15 min.
- **White-label config UI**: partner/child branding (colors, name, logo
  upload) with `branding.updated` audit; portal inherits it.
- **Revenue-leakage endpoint**: unbilled mapped spend + credit/commitment
  coverage gaps in one view.

Verification (2026-09-17):

- `ruff` clean; `mypy app` clean (76 files); `pytest` **75 passed, 6 skipped**
  (incl. 7 scheduled-report tests + pricing contract-mismatch regression);
  PostgreSQL RLS suite **6 passed**.
- Live smokes: `smoke_phase1_live.py` **SMOKE_OK** (25 steps),
  `smoke_phase2_live.py` **SMOKE2_OK** (17 steps: notes, waiver→approve→close,
  credits, commitments, leakage, report exports, branding).
- Scheduled reports verified against live Postgres + MinIO: due schedule →
  7-row CSV stored, sha256 recorded, outbox entry created, `next_run_at`
  advanced, audit written.
- Playwright journey **13 passed, 1 skipped** (AI-assistant step, Phase 5).
- `pnpm typecheck` 0 errors; vitest 8 passed; `next build` clean.

### Phase 2 bug class fixed along the way

- **Stale async select race** (Playwright step 7 → 500): the pricing page's
  contract fetch could land out of order and keep the previous customer's
  contract version selected; the pricing service then wrote a run row whose
  org_path (taken from the contract) failed RLS. Fixed on both sides: the UI
  cancels stale fetches, and `run_pricing` rejects a contract version that
  doesn't belong to the customer with a clean 409. Regression test added.
- **RLS deny-all for workers**: `run_due_schedules` initially saw zero rows
  because it queried before binding any org scope. Workers now use the
  documented bypass scope for the cross-tenant due pass, then bind each
  schedule's own org scope for generation (same pattern as the seed script).

## Phase 3 — Azure and multi-cloud ingestion (COMPLETE)

Shipped:

- **Azure adapter** behind the same ingestion contract as AWS (clean-room
  synthetic export format, deterministic 3-month fixtures: subscriptions +
  resource groups, reservations, EA credit + refund, planted duplicate,
  orphan subscription, +$180 true-up, late adjustment).
- **Multi-cloud per customer**: demo customers now carry AWS accounts *and*
  Azure subscriptions; pricing runs and invoice lineage aggregate both
  providers (lineage verified via `/invoices/{id}/lineage`).
- **Provider bill totals per account** (`level` = invoice|account; recon
  leg A stays invoice-level — no double count) + `/reconciliation/bill-totals`.
- **Connector scheduling**: `provider_connectors` (RLS), API CRUD-lite +
  run/toggle gated on `integration.manage`, Celery beat every 15 min with
  bypass-discovery + per-org scope, audited lifecycle. Synthetic mode only —
  `fetch_available=false` reported honestly end-to-end (ADR-0016); no fake
  pull buttons.
- **UI**: Azure import button + connectors panel on Cloud Accounts;
  `CURRENT_PHASE=3`; nav honesty maintained (Integrations page = Phase 5).

Verification (2026-09-17):

- `ruff` clean; `mypy` clean (81 files); `pytest` **100 passed** incl. new
  `test_azure_ingestion.py` (13, with 2 hypothesis fuzz: no-crash + no
  silent drops + Decimal round-trip) and `test_phase3_connectors.py` (6);
  PostgreSQL RLS suite **6 passed** against live PG 16 after fresh migrate.
- Live smokes: phase 1 **SMOKE_OK** · phase 2 **SMOKE2_OK** · **NEW**
  `smoke_phase3_live.py` **SMOKE3_OK** (22 steps incl. merged recon delta
  4680.00 = AWS 4500 ⊕ Azure 180 and dual-provider lineage).
- Playwright journey **14 passed, 1 skipped** (assistant = Phase 5), new
  step 2b (Azure import, orphan discovery, honest connector panel), run
  twice consecutively.
- `pnpm typecheck` 0 errors; vitest 8; `next build` clean.

## Phase 4 — FinOps & governance (COMPLETE)

Shipped:

- **Budgets** (partner/customer scope) with straight-line burn projection —
  closed periods report actual, never phantom-projected. Portal-safe
  customer projection at /portal/budgets.
- **Anomaly detection**: robust MoM median/MAD z-score with abs + relative
  floors; every row persists its method and evidence; re-runs upsert and
  auto-resolve stale signals (ADR-0017). /portal/anomalies shows customers
  only 'their spend changed, here is the size'.
- **Forecasting** (`avg_mom_growth_linear`, method in payload), **unit
  economics** per application/environment/owner/cost-center, **tag
  coverage** + unallocated-cost totals.
- **Recommendations**: idle leftover, non-prod right-size, commitment gap
  (estimated from the discount factor observed in the same book),
  marketplace review — each with basis + confidence; accept/dismiss audited;
  **savings realization is measured, never projected** (stays 0 until
  post-decision data exists).
- **Governance**: policy engine over billing facts → findings with evidence
  and lifecycle (open/acknowledged/excepted/remediated/reopened); time-boxed
  exceptions auto-revoke. Provider-config kinds exist in the model but
  evaluate to zero rather than being faked.
- **UI**: Budgets & Anomalies, Optimization, Governance console pages;
  portal Budgets + Cost Changes; FinOps nav group live; nightly beat pass
  (system-audited). `CURRENT_PHASE=4`.

Verification (2026-09-18):

- `ruff` clean; `mypy` clean (91 files); `pytest` **115 passed** (new
  `test_finops_phase4.py` ×9, `test_phase4_api.py` ×6); RLS **6 passed**
  after fresh drop→migrate(`8bd7518cfa18`)→seed; six new tables verified
  RLS-enabled.
- **NEW** `smoke_phase4_live.py` **SMOKE4_OK** (24 steps incl. the planted
  Cobalt spike surfacing, the noise-drop regression, realized-savings
  honesty, portal leak checks). Smokes 1–3 re-passed on the reset DB.
- Playwright journey **15 passed, 1 skipped** (assistant = Phase 5); new
  step 10b (anomaly pass + governance evaluate in the UI), run twice.
- Web: typecheck 0; vitest 8; `next build` clean; headless probe: all five
  new pages render with data, 0 page errors.

## Phase 5 — AI assistant & integrations (COMPLETE)

Shipped:

- **Assistant (deterministic retrieval, ADR-0018)**: 10 read-only tools
  behind an intent router (invoice change drivers, rules-on-invoice,
  lineage, margin-below-target, unbilled accounts, unallocated credits,
  recon difference, verified savings, anomaly summary, customer-safe
  drafts). Facts vs estimates separated; every answer cites record ids;
  unmatched questions refused with a reason. No LLM in the path —
  `mode=deterministic_demo` labeled in API/UI/audit.
- **Audit**: every query (incl. refusals) → `AIQueryAudit`; sensitive
  intents → additional `audit_events`; `/assistant/audit` for auditors
  (403 for portal users). Console `/assistant` + portal `/portal/assistant`
  (own-scope answers only; partner intents refused).
- **Webhooks**: HMAC-SHA256 signed deliveries (`X-CPPartnerOps-Signature`
  over `timestamp.body`, Idempotency-Key), secret shown once then masked,
  retry-capped beat sweep (5 min) + manual sweep endpoint, SSRF validation
  (metadata always refused; private only in local/test). Real events:
  `invoice.issued`, `dispute.created`, `report.ready`, `integration.test`;
  ancestor-chain endpoint matching with per-caller RLS scope.
- **Email transport**: outbox flush → `logs/notifications.ndjson` (working
  local transport, same seam as SMTP adapters in deployments).
- **ERP export**: `GET /invoices/{id}/export?fmt=json|csv` — `cpo.erp.v1`
  journal doc, customer-visible lines only (provider cost/margin/internal
  notes structurally excluded), audited + ExportJob. Buttons on invoice page.
- **Integrations page**: endpoints + test-send + deliveries, honest external-
  system registry (`connected` only from real checks), API-token guidance.
- Live fixes: bearer-token expiry naive-datetime 500 (deps.py), global-sweep
  head-of-line blocking on test-send, queue_event ancestor matching.

Verification:

- `ruff`/`mypy` clean (96 files); `pytest` **125 passed** (new
  `test_phase5.py` ×10 incl. receiver-side HMAC verification against a real
  local HTTP listener).
- `smoke_phase5_live.py` **SMOKE5_OK** (15 steps, re-runnable). Smokes 1–4
  re-passed (25/17/22/24).
- Playwright journey **18 passed, 0 skipped** — assistant/ERP/webhook legs
  (12, 12b, 12c) now real; ran twice consecutively.
- Web: typecheck 0; vitest 8; `next build` clean (assistant, integrations,
  portal/assistant prerender).

## Next: Phase 6 — hardening & deployment

Auth hardening (SSO/OIDC seam), KMS secret refs, DNS-pinned egress, load/
scale passes, backup/restore drills, staging deploy. (Remaining charter
scope after Phase 6 = polish; see charter phase table.)
