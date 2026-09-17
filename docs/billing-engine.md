# Billing Engine Specification

Version: engine `0.1.0` (see `calc_engine_version` setting; frozen per pricing run).
Implementation: `apps/api/app/engine/` + `apps/api/app/services/pricing_service.py`.

The engine is deterministic, explainable, and versioned. Given the same
canonical records, contract version, and rule versions, it always produces
byte-identical totals — no wall-clock, random, or float inputs.

## Data flow

```
CanonicalCostRecord rows (period, customer)
  → _aggregate()        group rows into CostSlices (invoice_grouping keys from the contract)
  → apply_rules()       ordered rule application per slice (lineage trace per rule)
  → invoice_level_adjustments()   minimum-monthly floor / maximum-cap on the total
  → PricingRunItem rows           one per slice + explicit min/cap residual lines
```

## Money

- Python `Decimal`, PostgreSQL `NUMERIC(20,6)`. Floats raise `TypeError` at
  rule-parameter load (`_num`).
- Two rounding modes: `engine()` keeps 6 dp during calculation (unit-rate
  fidelity); `q()` rounds to 2 dp only at invoice presentation.
- Every amount stored in a lineage record is the exact Decimal string.

## Ordering (rule precedence)

`sort_rules`: `(priority, calc_order, rule_id)` — all ascending, tie-broken by
UUID so ordering is total and reproducible. Filters decide *which* slices a
rule sees; order decides *when* it applies.

Two application bases per rule (`applied_basis`):
- `running_total` — the input is the running customer amount (stacks).
- contract basis (e.g. `provider_billed`) — the input is the slice's original
  contract-basis amount, so multiple rules compose additively instead of
  compounding.

## Supported rule types (Phase 1)

| type | math | mode |
|---|---|---|
| `percentage_markup` | `+pct% of basis` | additive |
| `percentage_discount` | `−pct% of basis` | additive |
| `fixed_unit_rate` | `rate × quantity` | replaces slice amount |
| `fixed_recurring` | `+ amount` (or replaces with `parameters.replaces=1`) | both |
| `one_time` | `+ amount` | additive |
| `managed_service_fee` | `+ pct% of basis + flat` | additive |
| `tax_adjustment` | `+ pct% of basis` | additive |
| `credit_retention` | keeps `pct%` of the negative credit for the partner, passes the rest | additive delta |
| `credit_pass_through` | passes `pct%` of the credit to the customer | additive delta |
| `support_charge` | `pass_through` (no-op) / `remove` (−support_fee) / `replace` (amount−support_fee) | delta |
| `sku_override` / `marketplace_adjustment` / `custom_service_charge` | `+ amount` | additive |
| `promotional_credit` | `−|amount|` (always negative) | additive |
| `manual_adjustment` | `+ amount` — publish requires approval (maker-checker enforced at rule-publish/activation) | additive |
| `tiered` | Σ band_qty × band_rate over `tiers:[{up_to,rate}]`, last rate extends infinitely | replaces slice amount |
| `data_exclusion` | amount → 0 (usage hidden from customer view, kept in lineage) | replaces |
| `minimum_monthly` / `maximum_cap` | invoice-level floor/ceiling on the total (see below) | invoice-level |

Unknown rule types raise — they never silently no-op.

### Minimum monthly & maximum cap (invoice level)

Applied to the summed customer total, in rule order, each producing an
explicit trace step and a residual invoice line (`line_kind=min_charge` /
`cap`) — never a fudge factor:
- minimum: if `0 < total < floor`, add `floor − total`. A net-negative month
  (credits exceed usage) is NOT floored upward; it must become a credit note.
- cap: if `total > ceiling`, subtract the excess.

## Credits semantics

Provider credits are stored negative on canonical rows. By default (no credit
rule bound) they reduce the customer amount 1:1 (pass-through). `credit_retention`
keeps a configured percentage for the partner and passes the remainder;
`credit_pass_through` explicitly controls the passed fraction. Partner vs
customer savings therefore always sum to the raw credit magnitude.

## Lineage (per PricingRunItem)

```
source_record_ids[]  contract_version_id  rule_version_ids[]
input_summary        formula (per rule: "markup 12.00% of 39467.19 = +4736.06")
calculation_trace[]  {rule, v, action, input, output, delta, formula}
output_amount        provider_cost_amount (partner-only field)
engine_version       timestamp, actor, approval_status
```

Answering "can this invoice amount be traced to source usage and a versioned
rule?" is a read of one row: `GET /api/v1/invoices/{id}/lineage?line_number=N`.

## Reprocessing & supersede

`POST /pricing/runs` for an existing customer-period creates run N+1 and marks
run N `superseded`; superseded runs are never mutated. Draft invoices linked
to a run are recalculated only while status allows (`draft`/`calculated`);
issued invoices are immutable by DB trigger — corrections go through credit/
debit notes (Phase 2) — so reprocessing can never change a delivered bill.

## Sandbox

`POST /billing-rule-versions/{id}/sandbox` runs one rule version against
historical canonical data inside the caller scope WITHOUT creating a
PricingRun; the projected baseline/sandbox totals are persisted in
`rule_sandbox_tests` as evidence attached to the approval.

## Multi-currency

Phase 1 operates per customer contract currency (USD in demo data). Rows whose
currency differs from the contract currency are quarantined at ingestion
(`invalid_currency`) rather than silently converted. The `currency_conversion`
rule type is reserved for Phase 2 and rejected by the engine today.

## Determinism tests

- `tests/test_engine_rules.py` — precedence, tier boundaries, min/max, credit
  splits, negative amounts, float rejection.
- `tests/test_money.py` — rounding modes, 20-digit precision.
- `tests/test_golden_billing.py` — fixed input/contract/expected-charge
  fixtures (charter "golden fixtures").
- `tests/test_e2e_pipeline.py` — full seed→ingest→price→invoice→recon chain.
