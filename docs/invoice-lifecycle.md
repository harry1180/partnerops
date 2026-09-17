# Invoice Lifecycle

States and transitions enforced in `app/services/invoice_lifecycle.py` and at
the database level (trigger `trg_invoice_immutable` in migration
`5ae8b26da9ad`).

```
draft ──▶ calculated ──▶ under_review ──▶ approved ──▶ issued ──▶ exported
                                        ▲     │                      │
                                        │     ▼                      ▼
                                   (return)  disputed ◀────▶ paid_or_settled
                                                   │
                                                   ▼
                                               corrected  (via credit note, Phase 2)
   voided  — only from draft/calculated
```

| status | meaning | mutability |
|---|---|---|
| `draft` | created from a pricing run, numbers may still be regenerated | editable |
| `calculated` | pricing totals frozen into lines; awaiting review | financial fields frozen |
| `under_review` | a reviewer is checking; can be returned to `calculated` | no money changes |
| `approved` | authorized to issue; `approved_by` recorded | immutable except status |
| `issued` | delivered to the customer (portal-visible, PDF/CSV valid) | **fully immutable** |
| `exported` / `paid_or_settled` | downstream ERP/payment state mirrors (integration boundary only; no payment processing in-product) | status only |
| `disputed` | customer filed a dispute; stays delivered | status only |
| `corrected` | superseded by a linked correction (credit/debit note — Phase 2) | immutable |
| `voided` | cancelled before issue | immutable |

## Immutability rules

1. Transition legality is a whitelist; anything else → `409 illegal_transition`.
2. From `issued` onward, a DB trigger rejects UPDATEs to any financial column
   (`subtotal`, `discounts_total`, `credits_total`, `fees_total`,
   `adjustments_total`, `taxes_total`, `prior_period_adjustments_total`,
   `total`, `contract_version_id`, `run_id`) and to `lines` of the invoice —
   even for table owners. Only `status`, `notes_*`, timestamps may change.
3. Corrections to issued invoices: credit note / debit note / replacement
   invoice with full linkage (`Invoice.corrects_id`, `line.pricing_run_item_id`
   lineage) — implemented in Phase 2; the API today rejects direct edits and
   the docs state the boundary rather than fake it.
4. Every transition writes `invoice.state_changed` to the append-only audit
   trail with actor, correlation id, from→to, and optional note.

## Numbering

`{PREFIX}-{YYYYMM}-{SEQ:04d}` per organization+pattern (configurable in
branding, e.g. `DEF` → `DEF-202606-0012`). The sequence row is locked with
`SELECT … FOR UPDATE` so concurrent issuance cannot collide, and the DB keeps
a unique constraint on (org_path, invoice_number) as the final guard.

## Customer visibility

Invoice lines carry `customer_visible`. The portal router
(`app/api/v1/portal.py`) projects only whitelisted fields and only lines where
`customer_visible` is true — internal notes, provider cost, and margin columns
are absent from every portal payload (asserted by Playwright journey step 11
and the live smoke).
