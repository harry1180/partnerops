# Demo Script — Cloud PartnerOps (Phase 1)

Everything below runs locally with no cloud credentials. Time: ~12 minutes.
Prereqs: `make up` (or docker compose for postgres/redis/minio), API on
:8001 (`apps/api` → `python run_server.py --port 8001`), web on :3000
(`pnpm --filter @cloudpartnerops/web dev`). Password for all demo accounts:
`.env → SEED_DEMO_PASSWORD`.

## Act 1 — The partner's question: "what did the provider charge, and who owes us?"

1. Sign in as **Morgan Northwind** (`msp@northwind-msp.example.com`).
   Executive Overview: org tree, customers, live totals.
2. **Cloud Accounts** → *Import synthetic AWS data* → three CUR months
   ingest (idempotent — re-running reports "already imported"). The
   `999999999999` row shows **unmapped**: a forgotten linked account with
   real spend. Click **Map** → assign to Testco/your family → status flips
   to mapped; ingestion re-attribution means the next pricing run bills it.
3. **Usage Explorer** — group provider spend by service / cost category /
   region / environment. Server-side aggregation only.

## Act 2 — Contract-aware pricing

4. **Contracts** → *New contract* for Acme: pricing basis provider-billed,
   minimum monthly 1,500.00. Draft v1.
5. **Billing Rules** → *New rule* `percentage_markup` 12%. Before publishing,
   hit **Test rule** → pick June → the sandbox shows baseline vs projected
   and the exact delta — *read-only, no run persisted, evidence stored*.
   Publish v2. Back to the contract: **Bind rules** → pin the published
   version → **Activate**.
6. **Pricing Runs** → customer Acme, June → *Run pricing* → totals:
   provider cost, customer billed, credits, margin — plus the lineage table
   (every line: group, rules applied, amounts). *Create invoice from this
   run*.

## Act 3 — Invoice lifecycle & immutability

7. On the invoice: *Send to review* → *Approve* → *Issue*. Try to go back:
   issued→draft is refused (409 in UI, DB trigger underneath). Download
   **PDF** and **CSV** — white-labeled (Northwind shows as "Northwind
   CloudBill" via branding config).
8. Open **lineage** on any line: source record ids, rule versions, formula
   strings, engine version, run number. This is the audit answer.

## Act 4 — Reconciliation & leakage

9. **Reconciliation** → run August → exceptions appear: the unmapped account,
   the quarantined duplicate row, the late July adjustment; materiality
   flags; resolve one with a note. Data-quality panel: missing periods,
   quarantine counts, failed jobs.
10. **Margins** → revenue vs provider cost vs margin per customer, low/
    negative-margin badges, unbilled-usage leakage signals.

## Act 5 — The customer's side

11. Sign out. Sign in as **Alex Acme** (`admin@acme-cloud.example.com`).
    Portal shows only: cost overview, usage, the issued invoice — no margin,
    no provider cost, no internal notes (the payload literally lacks the
    fields). Open the invoice → **Dispute** → file it.
12. Sign back in as Morgan → **Audit Trail** shows the whole story:
    ingestion, contract activation, publish, pricing run, every invoice
    state change, the dispute — append-only, correlation-ID'd.

## Act 4b — Operations: maker-checker, notes, close, schedules

11. **Approvals** (as Dana, the finance lead, *not* the rule's author): a
    high-impact rule publish or period close appears in the queue. Approving
    as a different person is what lets it through — the same person cannot
    approve their own submission (409). Waiving a material exception requires
    a reason code and is fully audited.
12. **Invoice detail → Notes**: add a credit note to an *issued* invoice.
    The invoice total corrects; the original stays immutable; the note links
    back to the invoice with its own audit trail.
13. **Period close**: close the customer's period — blocked while material
    reconciliation exceptions are open; resolve or waive, then close. Reopen
    keeps history.
14. **Credits & Commitments**: provider credits, Savings Plans and RIs with
    allocation policies, coverage/utilization computed from the same cost
    rows as the dashboards. (Track & allocate only — the platform never
    purchases or changes provider commitments.)
15. **Reports**: five CSV reports, each download audited. Create a *schedule*
    (monthly, day 17): the Celery beat pass generates the CSV into object
    storage (sha256 recorded), queues an outbox notification, advances
    `next_run_at`, and writes `report.schedule_ran` to the audit trail.
16. **Administration → Branding**: change the partner's colors/name —
    `branding.updated` is audited; the customer portal inherits it.

## Act 4c — Multi-cloud ingestion (Phase 3)

17. **Cloud Accounts → Import synthetic Azure data**: the same org now
    carries Azure subscriptions alongside AWS accounts. The import ingests
    3 months of clean-room cost-export fixtures through the *identical*
    pipeline (sha256 files → adapter → canonical rows → per-account bill
    totals). An orphan subscription the partner forgot about auto-appears
    as unmapped — the #1 unbilled-usage source — and reconciliation reports
    the enrollment's off-line true-up delta alongside the AWS one.
18. **Provider connectors panel**: AWS CUR + Azure Cost Export connectors,
    each showing mode (`synthetic fixtures`), cadence, last real ingestion
    (backed by an actual file row), next-due timestamp, and Run now. The
    "(no live pull)" label is the honesty marker: live credential fetching
    is Phase 5; nothing here pretends to reach a cloud.
19. **BlueRiver invoice → lineage**: one customer invoice mixes EC2 and
    Azure VM charges; the lineage view traces each line back to source
    records in *both* provider files.

## The isolation punchline

13. Sign in as **Casey Cascade** (`msp@cascade-it.example.com`, the other
    MSP): Customers list is empty, Acme's invoice URL returns 404 — not 403,
    so existence itself isn't leaked. (RLS is also enforced below the app:
    `apps/api/tests/test_pg_isolation.py`.)

## Automation equivalents

- Full API-level workflow: `apps/api` → `python tools/smoke_phase1_live.py`
- Phase 2 ops workflow: `python tools/smoke_phase2_live.py` (notes, waiver →
  approve → close, credits, commitments, leakage, report exports, branding)
- Phase 3 multi-cloud workflow: `python tools/smoke_phase3_live.py` (Azure
  connector run, orphan discovery, per-account bill totals, merged recon
  delta, multi-cloud pricing + lineage)
- Browser journey (this script as a test): `npx playwright test`
- Golden numbers: `python -m pytest tests/test_golden_billing.py -q`
