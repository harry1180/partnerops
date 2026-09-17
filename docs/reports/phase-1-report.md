# Phase 1 Completion Report — Working AWS Billing MVP

Date: 2026-09-16 (gate passed)
Phase: 1 of 6 · Status: **COMPLETE**

## What works now (end-to-end, no cloud credentials)

The charter's central workflow runs demonstrably on local synthetic data:

**Ingestion** — Deterministic synthetic AWS CUR (3 months, 186 canonical
rows/mo) via `POST /ingestion/synthetic/load` or real CSV upload
(`POST /ingestion/aws/upload`). Idempotent (content-hash dedupe at file and
row level), restartable, quarantine of invalid rows, late-adjustment
detection, account auto-discovery, provider-summary leg for reconciliation,
raw files preserved with checksum/parser-version/status lineage.

**Allocation** — Customers → account families → cloud accounts. Unmapped
accounts surface as a worklist (`/cloud-accounts` UI) with map/exclude
actions that re-attribute already-ingested costs and write audit events.

**Contracts & rules** — Versioned contracts (used versions never rewritten;
overlap guard; activation supersedes prior versions). 20 rule types with
filters, priority/calc_order, Decimal-only parameters. Rule Test sandbox
previews a rule's financial effect on history without persisting a run.

**Pricing** — `POST /pricing/runs` (202 + polling) freezes contract+rule
versions and produces per-line lineage: source record ids, per-rule formula
traces, engine version. Reprocessing supersedes; history immutable.

**Invoicing** — Lifecycle draft→calculated→under_review→approved→issued→
exported/settled/disputed/corrected/voided with DB-trigger immutability
(issued→draft = 409). PDF + CSV downloads, configurable numbering
(`DEF-YYYYMM-####`, per-org sequences, race-safe), white-label branding.

**Reconciliation** — Three-way (provider bill ↔ canonical ↔ invoices) with
tolerances, materiality, exception workflow (open→investigating→resolved/
waived), and a data-quality dashboard (missing periods, unmapped,
quarantine, failed jobs).

**Margins** — Revenue, provider cost, margin, margin %, negative/low-margin
flags, unbilled-usage leakage, per-customer trend API.

**Portal** — Customer-scoped whitelisted projections only; no margin,
provider cost, or internal notes in any payload (asserted at API + UI).
Disputes: file (customer) → resolve (partner); customer cannot self-resolve.

**Audit** — 14+ action types across the workflow, append-only (grants +
trigger), correlation IDs end-to-end.

## Verification (all re-run at this gate)

| Gate | Result |
|---|---|
| ruff / mypy | clean (70 files) |
| pytest | 63 passed, 6 skipped (PG-only) |
| PostgreSQL RLS suite | 6 passed (live docker PG) |
| Live HTTP workflow smoke | 25 steps, SMOKE_OK |
| **Playwright journey** | **13 passed, 1 skipped** (assistant = Phase 5), 26s, re-runnable via `CPO_TAG` |
| typecheck / vitest / next build | 0 errors / 8 tests / 19 pages |

## Bugs the journey caught (and fixed) — why E2E exists

1. Tailwind never compiled (no PostCSS config) — pages were unstyled while
   every build gate stayed green (ADR-0013).
2. `packages/ui` outside Tailwind auto-source → Modal `fixed inset-0` missing
   → overlay collapsed, buttons unreachable.
3. Add-customer modal: org dropdown never populated (async race).
4. Account mapping 500 (`org_id` not a mapped column on canonical rows).
5. Select-clobber race: late-resolving fetches overwrote user selections —
   silently priced the wrong customer (3 modals fixed).
6. Rate limiter (10/min) tripped by ~15 journey logins → 60/min in demo env;
   tests now pin the limit instead of assuming it.
7. `next build` clobbering a running `next dev` — permanently fixed via
   separate `distDir` (verified by rebuilding under live dev traffic).

## Known limitations (honest)

- Maker-checker enforcement UI, credit/debit notes, period-close endpoint,
  tiered demo contract, multi-currency conversion, Azure adapter, scheduled
  reports, commitments/credits UI, white-label config UI → Phase 2/3.
- PDF layout is minimal (internal writer); charts (ECharts) arrive with the
  reporting layer.
- Journey step 12 (AI assistant) is skipped by design — no fake answers.

## Next phase

Phase 2 — Advanced billing operations: maker-checker approvals, credit/debit
notes, disputes→adjustment flow, period closing, commitment & credit
allocation policies, scheduled reports, revenue-leakage alerts, full
white-label administration.
