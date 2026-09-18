# Phase 4 report — FinOps & governance

Date: 2026-09-18 · HEAD: `phase-4` commit (after Phase 3 multi-cloud)

## Completed functionality

| Area | What shipped |
|------|--------------|
| Budgets | Per-scope (partner org or customer) monthly caps; actuals summed from canonical rows; straight-line burn projection (exact actual for closed periods — no phantom extrapolation); threshold/over states; soft delete; audited. |
| Anomaly detection | Robust month-over-month z-score (median/MAD of prior months) with three gates: \|z\| ≥ 4, \|delta\| ≥ $500, \|delta\| ≥ 50% of baseline; ≥2-month history required. Every row persists method + full evidence; re-runs upsert open rows and auto-resolve disappeared signals; reviewed states preserved. Method honesty per ADR-0017. |
| Forecasting | Average MoM growth, linear, labeled `avg_mom_growth_linear` in the response; `insufficient_history` when <2 months. |
| Recommendations | Billing-observable signals only: idle leftover, non-prod-vs-prod right-size, commitment gap (estimated from the discount factor actually observed in the same book), flat marketplace lines. Each carries `basis` evidence + honest `confidence`. Accept/dismiss audited. |
| Savings realization | MEASURED only: before/after monthly spend around an accepted decision; stays NULL (total 0) until post-decision data exists — smoke asserts the honest zero. |
| Unit economics + tag coverage | Cost per application/environment/owner/cost-center; coverage % per required tag key; unallocated-cost total. |
| Governance engine | Policies (required_tags, approved_regions, unallocated_cost, idle_resources, oversized_resources) evaluated over canonical rows → findings with evidence + dedupe lifecycle (open → acknowledged / excepted / remediated / reopened). Time-boxed exceptions; expiry auto-revokes and reopens. Provider-config kinds (public storage, encryption) evaluate to zero findings rather than being faked — documented in the module. |
| Portal (customer side) | /portal/budgets + /portal/anomalies: strictly whitelisted projections (their charges vs their cap; direction/service/month only — no partner cost language); RLS-bound to the customer subtree. |
| UI | Budgets & Anomalies, Optimization, Governance console pages (all controls wired); portal Budgets + Cost Changes; FinOps nav group un-gates at `CURRENT_PHASE=4`; nightly beat task (03:40 UTC) runs all passes per-org with system audits. |
| Docs | ADR-0017, this report, status/README/ERD/demo-script updates. |

## Tests & verification (all re-run 2026-09-18)

- `ruff` clean · `mypy` clean (91 files) · `pytest` **115 passed** incl. new
  `test_finops_phase4.py` (9: robust-z math, budget projection, and
  end-to-end passes on the planted Cobalt spike, including the noise-drop
  regression) and `test_phase4_api.py` (6: RBAC + full flows over login).
- PostgreSQL: fresh DROP SCHEMA → migrate (rev `8bd7518cfa18`) → seed; all
  six new tables verified `relrowsecurity=t`; RLS suite 6 passed.
- **NEW** `smoke_phase4_live.py` **SMOKE4_OK** (24 steps): seeded + created
  budget variance, anomaly pass finds the planted 14x and filters the noise
  drop, recommendation evidence + decision flow, realized-savings honesty
  (0 without post data), forecast/unit-economics/tag-compliance reads,
  governance evaluate → findings → exception, audit coverage, portal
  scoping + payload-leak checks.
- Live smokes 1–3 re-run on the reset DB: **SMOKE_OK / SMOKE2_OK /
  SMOKE3_OK**.
- Playwright journey **15 passed, 1 skipped** (assistant = Phase 5): new
  step 10b drives Budgets (seeded cap, anomaly pass) and Governance
  (evaluate, evidence dialog) in the UI; passed twice consecutively.
- Web: typecheck 0 · vitest 8 · `next build` lists the five new routes ·
  headless probe: /optimization, /budgets, /governance, /portal/budgets,
  /portal/anomalies all render with data, 0 page errors, nav live.

## Bugs found & fixed this phase

1. **Noise-drop false positive** (found by the live pass): a -7.8% wobble
   scored z=-6 on a 2-month baseline because MAD was tiny. Fixed with the
   relative-change gate (≥50% of baseline) — encoded as a regression test
   and documented in ADR-0017.
2. **Smoke re-runnability**: the first Phase 4 run acknowledged the CloudFront
   spike, so a second run found no open CloudFront row. Fixed by reading the
   status filter and handling the previously-reviewed path.
3. **Portal scoping**: partner-created customer budgets are stored under the
   customer's own org path (still inside the partner scope) so the RLS-bound
   portal can read exactly its own rows.

## Known limitations at this phase gate (honest list)

1. Detection runs at monthly grain because that is what the demo fixtures
   carry; daily-grain series need real feeds (seam documented in ADR-0017).
2. Governance sees billing facts only: encryption posture, public exposure,
   and overly-permissive IAM require provider-config connectors (planned
   with integrations) — those kinds exist in the model but evaluate to zero.
3. Alerting/notification for budget breaches rides the Phase 5 notification
   outbox integration (statuses compute correctly today, delivery is the
   integration boundary).
4. Rightsizing is a spend-volume heuristic (no utilization telemetry);
   confidence is labeled `medium` and the basis shows the arithmetic.
5. `oversized_resources` threshold semantics are "sustained total ≥ 3× the
   monthly threshold" — deliberately coarse until utilization data exists.

## Next: Phase 5 — AI assistant & integrations

Permission-aware AI assistant (deterministic demo mode, retrieval + cited
internal records), scoped API tokens + rotation, signed webhooks, ERP export
formats, notification delivery behind the existing outbox, and the
Integrations admin page (nav currently honest-gated at Phase 5).
