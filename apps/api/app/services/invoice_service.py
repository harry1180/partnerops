"""Invoice builder: PricingRun → Invoice + InvoiceLines (draft/calculated).

Support policy from contract:
- pass_through: customer sees support at cost (added to invoice total)
- remove: support stripped from both customer charge and internal cost view
- replace: contract's support_charge rule already handled it in pricing

Tax policy similar; credits already flowed through credit rules during
pricing. Prior-period adjustments: late-arriving canonical rows priced under
their file's period but with older usage_start get their own line kind.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_scope
from app.engine.money import ZERO, q
from app.models.billing_core import Customer
from app.models.contracts import ContractVersion
from app.models.invoices import Invoice, InvoiceLine, InvoiceSequence
from app.models.pricing import PricingRun, PricingRunItem
from app.models.reconciliation import ProviderBillTotal
from app.services.audit_service import record_audit

LINE_KIND_ORDER = {
    "usage": 0, "discount": 1, "credit": 2, "service_fee": 3,
    "min_charge": 4, "cap": 5, "prior_period": 6, "adjustment": 7,
    "support": 8, "tax": 9, "exclusion": 10,
}


class NumberConflictError(Exception):
    pass


async def next_invoice_number(session: AsyncSession, org_path: str,
                              period_start: datetime, pattern: str = "DEF") -> str:
    """Pattern placeholders: {PREFIX}{YYYY}{MM}{SEQ:04d} — seq per org+pattern."""
    seq = (
        await session.execute(
            select(InvoiceSequence)
            .where(InvoiceSequence.org_path == org_path, InvoiceSequence.pattern_key == pattern)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if seq is None:
        seq = InvoiceSequence(org_path=org_path, pattern_key=pattern, next_value=1)
        session.add(seq)
        await session.flush()
    number = f"{pattern}-{period_start:%Y%m}-{seq.next_value:04d}"
    seq.next_value += 1
    return number


async def build_invoice_from_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    principal_org_path: str,
    actor_user_id: uuid.UUID | None,
    correlation_id: str | None,
) -> Invoice:
    run = await session.get(PricingRun, run_id)
    if run is None or run.status != "completed":
        raise ValueError("pricing run not completed")
    customer = await session.get(Customer, run.customer_id)
    cv = await session.get(ContractVersion, run.contract_version_id)
    if customer is None or cv is None:
        raise ValueError("missing customer or contract version")

    await set_org_scope(session, customer.org_path)
    items = list((
        await session.execute(
            select(PricingRunItem)
            .where(PricingRunItem.run_id == run.id)
            .order_by(PricingRunItem.item_number)
        )
    ).scalars())
    if not items:
        raise ValueError("pricing run has no items")

    support_policy = str((cv.support_fee_policy or {}).get("mode", "pass_through"))
    tax_policy = str((cv.tax_behavior or {}).get("mode", "pass_through"))
    rounding = str((cv.rounding_rule or {}).get("mode", "half_up"))

    invoice = Invoice(
        org_id=customer.org_id, org_path=customer.org_path,
        invoice_number=await next_invoice_number(session, customer.org_path, run.period_start),
        customer_id=customer.id, contract_version_id=cv.id, pricing_run_id=run.id,
        period_start=run.period_start, period_end=run.period_end,
        currency=items[0].currency or cv.currency, status="calculated",
        grouping=cv.invoice_grouping or ["service"],
        payment_terms=cv.payment_terms,
    )
    subtotal = ZERO
    line_no = 0
    session.add(invoice)
    await session.flush()  # materialize invoice.id before child lines
    for it in sorted(items, key=lambda i: (LINE_KIND_ORDER.get(i.line_kind, 99), i.item_number)):
        kind = it.line_kind
        if kind == "support" and support_policy == "remove":
            continue
        if kind == "tax" and tax_policy == "remove":
            continue
        amount = q(it.output_amount or ZERO, mode=rounding)
        if amount == 0:
            continue
        line_no += 1
        prior_period = kind == "usage" and it.extra_metadata.get("prior_period") == "true"
        session.add(InvoiceLine(
            invoice_id=invoice.id, org_path=invoice.org_path,
            line_number=line_no, kind="prior_period" if prior_period else kind,
            group_key=it.group_key, description=_describe(it),
            quantity=Decimal(str((it.extra_metadata or {}).get("quantity", "0"))) or None,
            amount=amount, currency=it.currency,
            pricing_run_item_id=it.id, source_record_ids=it.source_record_ids[:MAX_SRC],
            rule_version_ids=it.rule_version_ids,
            customer_visible=True,
        ))
        subtotal += amount
        if kind == "usage" and not prior_period:
            invoice.subtotal = (invoice.subtotal or ZERO) + amount
        elif kind == "min_charge" or kind == "cap":
            invoice.adjustments_total = (invoice.adjustments_total or ZERO) + amount
        elif kind == "prior_period":
            invoice.prior_period_adjustments_total = (invoice.prior_period_adjustments_total or ZERO) + amount
        elif kind == "support":
            invoice.fees_total = (invoice.fees_total or ZERO) + amount
        elif kind == "tax":
            invoice.taxes_total = (invoice.taxes_total or ZERO) + amount
        elif kind == "credit":
            invoice.credits_total = (invoice.credits_total or ZERO) + amount

    invoice.total = q(subtotal, mode=rounding)
    invoice.provider_cost_total = sum((it.provider_cost_amount or ZERO for it in items), ZERO)
    invoice.margin_total = invoice.total - invoice.provider_cost_total
    session.add(invoice)
    await session.flush()
    await record_audit(
        session, None, action="invoice.created", org_path=invoice.org_path,
        summary=f"Invoice {invoice.invoice_number} calculated for {customer.display_name} "
                f"({invoice.currency} {invoice.total}) from pricing run #{run.run_number}",
        entity_type="invoice", entity_id=invoice.id,
        detail={"run_id": str(run.id), "amount": str(invoice.total)},
        actor_kind="system", correlation_id=correlation_id,
    )
    await session.commit()
    return invoice


MAX_SRC = 500


def _describe(it: PricingRunItem) -> str:
    if it.line_kind == "usage":
        return it.group_label or it.group_key
    labels = {
        "min_charge": "Minimum monthly charge adjustment",
        "cap": "Maximum charge cap adjustment",
        "support": "Support fees",
        "tax": "Taxes",
        "credit": "Credits applied",
        "adjustment": "Adjustment",
    }
    return labels.get(it.line_kind, it.group_label or it.line_kind)


async def provider_total_for(session: AsyncSession, org_path: str,
                             period_start: datetime) -> Decimal:
    """Leg 1 sum across billing accounts under this org for the period."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(ProviderBillTotal.billed_total), 0)).where(
                ProviderBillTotal.org_path == org_path,
                ProviderBillTotal.period_start == period_start,
            )
        )
    ).scalar_one()
    return Decimal(total or 0)
