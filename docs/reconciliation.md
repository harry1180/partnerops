# Reconciliation

Three-way match per provider billing period. Implementation:
`app/services/reconciliation_service.py`; UI: `Reconciliation` page.

```
Leg 1  Cloud-provider bill          (raw provider summary rows / CUR invoice totals)
Leg 2  Normalized internal cost     (sum of canonical cost records)
Leg 3  Customer invoices            (sum of issued/corrected invoice totals)
```

## What runs

For a period (and each billing account inside it, and each customer with
usage in it):

1. **Provider ↔ canonical (per account):** provider summary unblended total
   vs sum of `provider_billed` from ingested records (credits/tax/support
   compared component-wise).
2. **Canonical ↔ invoices (per customer):** provider-attributed cost vs
   customer billed revenue. Usage without an invoice (status issued/exported/
   paid) raises `uninvoiced_usage` — revenue leakage.
3. **Unmapped cost:** rows with no customer attribution raise
   `unmapped_account` — charges that no invoice can ever explain.
4. **Quarantine review:** parsed-but-rejected rows surface as
   `quarantined_rows`.
5. **Late adjustments:** canonical rows whose usage predates the period are
   flagged `late_adjustment` with lineage to the adjustment file.

## Exception model

`ReconciliationException` = type, materiality (`material|minor`), severity,
amount delta, machine explanation, evidence (file ids, run ids, account ids),
owner, status (`open → investigating → resolved|waived`), notes, resolution
timestamp. Deduped by `dedupe_key` (type+scope+period) so repeated runs update
rather than duplicate.

**Tolerances** are per run: `tolerance_abs` (default 0.000001) and a % basis
for rounding-class differences; anything beyond is `material`.

## Period close (gate)

`reconciliation.waived` requires the `recon.waive` permission (platform/distributor/
msp + billing analyst roles) and writes an immutable audit event with who,
what, and why. A period with **open material exceptions** cannot be closed
(Phase 2 adds the explicit `period.close` endpoint; the gate logic and
materiality data already exist and are surfaced in the UI).

## Where the difference numbers come from

Demo data deliberately breaks the legs so every path is exercised:
- the `999…` forgotten account → unmapped cost every period,
- one duplicate source row → quarantined (visible in leg-1 delta),
- one late adjustment (July usage billed in August) → late_adjustment,
- provider summary row = authoritative invoice total for leg 1.

The live smoke (`tools/smoke_phase1_live.py`) asserts June reconciliation
produces the expected open exceptions; Playwright journey step 9 runs
August from the UI.
