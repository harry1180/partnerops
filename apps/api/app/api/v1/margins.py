"""Margin & revenue-leakage analytics API (partner-only views).

margin.view is required — customer roles never see partner economics.
Reconciles the three legs per customer-period:
  provider cost (canonical usage+support+tax) ↔ invoiced revenue ↔ margin.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import Principal, SessionDep
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord
from app.models.invoices import Invoice

router = APIRouter()


async def _summary(session: SessionDep, principal: Principal,
                    period_start: datetime | None) -> list[dict]:
    root = principal.scope_prefixes[0]
    custs = (
        await session.execute(
            select(Customer).where(Customer.deleted_at.is_(None),
                                   Customer.org_path.like(root + "%"))
            .order_by(Customer.display_name)
        )
    ).scalars().all()
    out = []
    for c in custs:
        cw = [CanonicalCostRecord.customer_id == c.id]
        iw = [Invoice.customer_id == c.id, Invoice.deleted_at.is_(None),
              Invoice.status.notin_(("voided",))]
        if period_start:
            cw.append(CanonicalCostRecord.billing_period_start == period_start)
            iw.append(Invoice.period_start == period_start)
        usage = Decimal((await session.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0)).where(*cw)
        )).scalar_one() or 0)
        support = Decimal((await session.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.support_fee), 0)).where(*cw)
        )).scalar_one() or 0)
        tax = Decimal((await session.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.tax), 0)).where(*cw)
        )).scalar_one() or 0)
        credit_amt = Decimal((await session.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.credit), 0)).where(*cw)
        )).scalar_one() or 0)
        revenue = Decimal((await session.execute(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(*iw)
        )).scalar_one() or 0)
        issued_any = (await session.execute(
            select(func.count(Invoice.id)).where(*iw,
                Invoice.status.in_(("issued", "exported", "paid_or_settled", "corrected", "disputed")))
        )).scalar_one()
        provider_cost = usage + support + tax
        margin = revenue - provider_cost
        margin_pct = (margin / revenue * 100) if revenue else None
        out.append({
            "customer_id": str(c.id), "customer": c.display_name, "code": c.code,
            "target_margin_pct": float(c.target_margin_pct) if c.target_margin_pct is not None else None,
            "provider_cost": str(provider_cost), "usage": str(usage),
            "support": str(support), "tax": str(tax), "credits": str(credit_amt),
            "revenue": str(revenue), "margin": str(margin),
            "margin_pct": str(margin_pct) if margin_pct is not None else None,
            "unbilled_usage": usage > 0 and not issued_any,
            "invoiced": bool(issued_any),
        })
    return out


@router.get("/margins/summary")
async def margins_summary(session: SessionDep, principal: Principal,
                          period_start: datetime | None = None):
    if not principal.can("margin.view"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    rows = await _summary(session, principal, period_start)
    tot_rev = sum(Decimal(r["revenue"]) for r in rows)
    tot_cost = sum(Decimal(r["provider_cost"]) for r in rows)
    return {
        "period_start": period_start.isoformat() if period_start else None,
        "totals": {
            "revenue": str(tot_rev), "provider_cost": str(tot_cost),
            "margin": str(tot_rev - tot_cost),
            "margin_pct": str((tot_rev - tot_cost) / tot_rev * 100) if tot_rev else None,
            "low_margin_customers": sum(
                1 for r in rows
                if r["margin_pct"] is not None and Decimal(r["margin_pct"]) < Decimal("10")),
            "negative_margin_customers": sum(1 for r in rows if Decimal(r["margin"]) < 0),
            "unbilled_customers": sum(1 for r in rows if r["unbilled_usage"]),
        },
        "customers": rows,
    }


@router.get("/margins/trend")
async def margins_trend(session: SessionDep, principal: Principal,
                        customer_id: uuid.UUID | None = None):
    if not principal.can("margin.view"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    q = (select(
            CanonicalCostRecord.billing_period_start,
            func.sum(CanonicalCostRecord.provider_billed)
            + func.sum(CanonicalCostRecord.support_fee)
            + func.sum(CanonicalCostRecord.tax).label("cost"),
         ).where(CanonicalCostRecord.org_path.like(root + "%"))
         .group_by(CanonicalCostRecord.billing_period_start)
         .order_by(CanonicalCostRecord.billing_period_start))
    if customer_id:
        q = q.where(CanonicalCostRecord.customer_id == customer_id)
    cost_rows = (await session.execute(q)).all()
    iq = (select(Invoice.period_start, func.sum(Invoice.total).label("rev"))
          .where(Invoice.org_path.like(root + "%"), Invoice.deleted_at.is_(None),
                 Invoice.status != "voided")
          .group_by(Invoice.period_start))
    if customer_id:
        iq = iq.where(Invoice.customer_id == customer_id)
    rev_rows = {p: Decimal(r or 0) for p, r in (await session.execute(iq)).all()}
    periods: dict[str, dict] = {}
    for p, cost in cost_rows:
        key = p.isoformat()
        periods[key] = {"period": key, "provider_cost": str(cost or 0),
                        "revenue": str(rev_rows.get(p, Decimal("0")))}
    for p, rev in rev_rows.items():
        key = p.isoformat()
        periods.setdefault(key, {"period": key, "provider_cost": "0", "revenue": "0"})
        periods[key]["revenue"] = str(rev)
    series = sorted(periods.values(), key=lambda x: x["period"])
    for pt in series:
        pt["margin"] = str(Decimal(pt["revenue"]) - Decimal(pt["provider_cost"]))
    return {"points": series}


@router.get("/invoices/{invoice_id}/lineage")
async def invoice_lineage(invoice_id: uuid.UUID, session: SessionDep,
                         principal: Principal, line_number: int = Query(..., ge=1)):
    """Full calculation lineage for one invoice line — the audit answer to
    'can every invoice amount be traced to source usage and a versioned rule?'"""
    if not principal.can("invoice.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    from app.models.invoices import InvoiceLine
    from app.models.pricing import PricingRun, PricingRunItem

    root = principal.scope_prefixes[0]
    inv = await session.get(Invoice, invoice_id)
    if inv is None or (not principal.is_platform_admin and not inv.org_path.startswith(root)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    ln = (await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id,
                                  InvoiceLine.line_number == line_number)
    )).scalar_one_or_none()
    if ln is None or ln.pricing_run_item_id is None:
        raise HTTPException(404, detail={"code": "no_lineage",
                                         "message": "this line has no linked calculation"})
    item = await session.get(PricingRunItem, ln.pricing_run_item_id)
    run = await session.get(PricingRun, item.run_id) if item else None
    is_customer = not principal.can("margin.view")
    return {
        "line_number": ln.line_number, "description": ln.description,
        "amount": str(ln.amount),
        "source_record_ids": ln.source_record_ids,
        "rule_version_ids": ln.rule_version_ids,
        "formula": item.formula if item else None,
        "calculation_trace": item.calculation_trace if item else None,
        "engine_version": run.engine_version if run else None,
        "run_id": str(item.run_id) if item else None,
        "run_number": run.run_number if run else None,
        "contract_version_id": str(inv.contract_version_id),
        "pricing_basis": None,
        "provider_cost_amount": str(item.provider_cost_amount) if item and not is_customer else None,
    }
