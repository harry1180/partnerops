# Rule Precedence & Calculation Order

How the billing engine decides *which* rules apply to a cost slice and *in
what order* — the question every "why is my invoice X?" conversation ends at.

## Matching (which slices a rule sees)

A rule version carries a `filters` object; a slice matches only if **all**
present filter dimensions match (`app/engine/rules.py::rule_matches`):

```
customer / account_family / account (or subscription) / provider
service / sku (meter) / region / resource
usage_type / tag (key or key=value) / application / environment
owner / cost_center / billing_period
```

Empty filters = applies to every slice in the contract's scope. Wildcards are
not implicit: `null` means "no constraint on this dimension".

## Ordering (when rules apply)

```
ORDER BY priority ASC, calc_order ASC, rule_id ASC
```

- `priority` (int, default 10): coarse buckets. Reserve space:
  - 1–9   exclusions / overrides (data_exclusion, sku_override)
  - 10–19 normal markup / discount / fee math
  - 20–29 credits and support policy
  - 30–39 invoice-level floors and caps (min/max applied at total level)
  - 90+   manual adjustments (explicitly last-resort, approval-gated)
- `calc_order` (int): ordering inside a priority band; use multiples of 10 to
  leave insertion room — renumbering would require a new rule version, which
  is exactly the point.
- `rule_id` tie-break makes the order total and reproducible even if two rules
  share both integers (they shouldn't, but runs must be deterministic).

## Composition semantics

- Additive rules (`percentage_*`, fees, tax, promo) contribute a delta on
  their `applied_basis`: either the **running total** (stacking) or the
  **contract basis** of the slice (parallel composition — two 5% discounts on
  `provider_billed` remove 10% of provider cost, not 9.75%).
- Replacement rules (`fixed_unit_rate`, `tiered`, `data_exclusion`) set the
  amount; later additive rules then stack on the replaced amount.
- `minimum_monthly` / `maximum_cap` ignore slices and act on the invoice
  total; each emits its own residual line, so the trace always sums exactly
  to the invoice.

## Conflicts and safety rails

- Two rules of the same type both matching a slice is legal (they compose in
  order) but the sandbox delta preview shows the effect before publishing.
- Rules belong to exactly one contract (or the platform pool) — a rule cannot
  silently reach another customer.
- Contracts pin rule **versions**. Publishing v3 of a rule changes nothing
  until a new contract version binds it.
- High-impact publishes (estimated monthly delta above the configurable
  threshold) require maker-checker approval — the maker creates the version,
  a different principal with `rule.approve` publishes it (enforcement UI in
  Phase 2; API currently records sandbox evidence).

## Debugging order-of-magnitude questions

`GET /api/v1/invoices/{id}/lineage?line_number=N` returns the full
`calculation_trace`: every rule that touched the slice in exact order with
`input → formula → output → delta`. That is the answer to "which billing
rules affected this invoice?" without anyone reading code.
