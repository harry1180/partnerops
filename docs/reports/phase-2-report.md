# Phase 2 report — Advanced billing operations

Date: 2026-09-17 · Branch state: working tree → `phase-2` commit

## Completed functionality

| Area | What shipped |
|------|--------------|
| Maker-checker | High-impact rule publishes and period closes require approval by a different person; waiver path with reason codes; Approvals queue UI. |
| Credit/debit notes | Notes on issued invoices recompute the invoice total through linked, audited note records; the original invoice stays immutable. |
| Period close | Per-customer close/open; material reconciliation exceptions block close unless waived (audited); reopen keeps history. |
| Credits & commitments | Track provider credits, Savings Plans and RIs; allocation policies; coverage/utilization from canonical cost rows. The platform never purchases or changes provider commitments. |
| Reports | Five on-demand CSV reports sharing the dashboards' aggregations; every download audited (`export.generated`). |
| Scheduled reports | Monthly/weekly cadence; worker generates CSV, stores in object storage with sha256 lineage, writes `notification_outbox` entries, advances `next_run_at`, audits each run; Celery beat every 15 min. |
| White-label | Partner/child branding UI (colors, name, logo upload) with `branding.updated` audit; portal inherits branding. |
| Revenue leakage | One endpoint: unbilled mapped spend + coverage gaps. |

## Tests & verification (all re-run 2026-09-17)

- `ruff` clean · `mypy` clean (76 files) · `pytest` **75 passed, 6 skipped** · PostgreSQL RLS **6 passed**
- New suites: `test_phase2_ops.py` (4), `test_scheduled_reports.py` (7), pricing contract-mismatch regression (1)
- Live smokes: `smoke_phase1_live.py` **SMOKE_OK** (25 steps) · `smoke_phase2_live.py` **SMOKE2_OK** (17 steps)
- Playwright journey: **13 passed, 1 skipped** (AI-assistant step is Phase 5)
- Scheduled reports verified against live Postgres + MinIO end-to-end
- Web: `typecheck` 0 errors · vitest 8 · `next build` clean

## Bugs found & fixed this phase

1. **Stale async select race** — pricing page's contract fetch could land out of order, keeping the previous customer's contract version selected; the service then wrote a run row failing RLS (500). Fixed in UI (cancel stale fetches) and service (reject foreign contract version → clean 409). Regression test added.
2. **RLS deny-all for workers** — `run_due_schedules` saw zero rows because it queried before binding any org scope. Workers now use the documented bypass scope for the cross-tenant due pass, then bind each schedule's org for generation.
3. **Alembic on sqlite** — new migration's RLS statements are now Postgres-only guarded, so the sqlite test harness keeps working.

## Known limitations (honest list)

1. Notification delivery stops at the outbox — no SMTP/webhook sender in local dev by design.
2. Tiered/minimum/maximum rules are tested but not exercised by the demo contract.
3. Multi-currency rows are quarantined, not converted (conversion rule deferred).
4. PDF layout is minimal (internal writer); rich branded layout deferred.
5. Beat schedule only runs when a Celery worker+beat are started (`make worker`); the API process does not self-schedule.

## Next: Phase 3 — Multi-cloud ingestion

Azure Cost Management + GCP Billing BigQuery adapters behind the same
ingestion contract, connector scheduling, file-format fuzz tests, provider
bill totals reconciliation per account.
