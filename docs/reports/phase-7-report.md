# Phase 7 report — spend alerts wired end-to-end

Date: 2026-09-18 · follow-up after the 0–6 charter: closes the deferred
notification loop and the last two unused seams.

## Why

Phase 4 shipped budgets with `alert_threshold_pct` + an `alert.manage`
permission, and its report listed "breach notifications ride the Phase 5
outbox" as a deferred limit. Phase 5 shipped that outbox + webhook fan-out —
but nobody produced `budget.over_threshold`, and `alert.manage` and
`ingestion.parsed` were declared-but-unused. This phase closes exactly those
gaps (no new feature surface invented).

## Shipped

- **Alert episodes** (`app/services/alerts.py`): evaluated per org subtree in
  the nightly FinOps pass + on demand (`POST /budgets/evaluate-alerts`,
  `alert.manage`). Crossing the threshold or exceeding the cap opens one
  episode: queues the signed `budget.over_threshold` webhook + emails the
  role-routed recipients. Re-evaluating while breached sends **zero**
  duplicates; recovery closes the episode and the next breach re-arms
  (episode counter). State lives in `budgets.alert_state` (JSON column,
  migration `0f3ab9c41d77`) — no new tables, no new RLS surface.
- **Notification routing**: partner alert roles (msp/distributor admin,
  finops analyst) in the evaluated subtree, plus the budget's own
  customer_admins for customer-scoped budgets. billing_analyst gets nothing
  (asserted). Emails ride the Phase 5 local transport; the manual sweep
  endpoint now runs both sweeps (deliver + flush) like the beat does.
- **orphan.discovered event**: `ingest_csv` queues it (signed webhook) when
  a file surfaces unmapped accounts — the actionable version of the unused
  `ingestion.parsed` ping (event catalog updated; per-file pings would be
  noise). Coverage: a fresh synthetic org with one mapped account → exactly
  one delivery row listing 222…222 but not 111…111.
- UI: "Run alert pass" button (alert.manage), "alert sent · episode #N"
  marker on breached rows; budgets feed API returns `alert_state`.

## Verification

- pytest **136 passed** (5 new phase-7 tests: full episode state machine
  open→stay→recover→re-arm with payload parity vs the UI math, orphan event
  row + sealed secret at rest, role-routed recipients, API flow over the
  demo Cobalt cap incl. duplicate suppression + audit, RBAC refusals).
- `smoke_phase7_live.py` **SMOKE7_OK — 11 steps, twice** (real listener
  verifies the alert HMAC receiver-side; emails confirmed in
  logs/notifications.ndjson with correct recipients).
- Smokes 1–5 re-passed; Playwright journey **18 passed** with the alert leg
  in step 10b; ruff/mypy clean (98 files); typecheck + vitest green.
- Migration applied to the live stack (`8bd7518cfa18 → 0f3ab9c41d77`);
  alert_state column verified.

## Honest limits

- Alert grain follows the budget window math (same straight-line projection
  the UI shows) — hourly-grain alerts would need usage-stream ingestion,
  unchanged scope decision from Phase 4.
- Email is the local transport file (per Phase 5); SMTP adapters share the
  same seam.
