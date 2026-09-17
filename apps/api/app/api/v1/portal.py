"""Customer portal API — a strictly-restricted projection.

Separate router on purpose: even if an internal endpoint later grows a
partner field by accident, portal responses are built here from whitelisted
projections only. Rules enforced:
- caller must be a customer-scoped user (not partner staff with margin.view).
- NO provider cost, NO margin, NO internal notes, NO other customers.
- usage/invoices scoped to the caller's own customer org path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, RequestPrincipal, SessionDep
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord
from app.models.invoices import Invoice, InvoiceLine

router = APIRouter()


def _require_customer_scope(principal: Principal) -> RequestPrincipal:
    if principal.is_platform_admin or "margin.view" in principal.permissions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={
            "code": "portal_for_customer_users",
            "message": "partner staff use the console endpoints; this is the portal API",
        })
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    return principal


CustomerPrincipal = Annotated[RequestPrincipal, Depends(_require_customer_scope)]


async def _my_customer(session: AsyncSession, principal: RequestPrincipal) -> Customer:
    """The Customer row for the signed-in customer user (matched by org path)."""
    cust = (
        await session.execute(
            select(Customer)
            .where(Customer.org_path == principal.org_path, Customer.deleted_at.is_(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if cust is None:
        raise HTTPException(404, detail={"code": "no_customer"})
    return cust


@router.get("/portal/customer")
async def my_customer(session: SessionDep, principal: CustomerPrincipal):
    cust = await _my_customer(session, principal)
    return {"id": str(cust.id), "name": cust.display_name, "code": cust.code}


_GROUP_RE = "^(service|cost_category|region|environment|application)$"


@router.get("/portal/usage/summary")
async def portal_usage(session: SessionDep, principal: CustomerPrincipal,
                       period_start: datetime | None = None,
                       group_by: str = Query("service", pattern=_GROUP_RE)):
    cust = await _my_customer(session, principal)
    col = getattr(CanonicalCostRecord, group_by)
    stmt = (select(col, func.sum(CanonicalCostRecord.provider_billed).label("amt"))
            .where(CanonicalCostRecord.customer_id == cust.id,
                   CanonicalCostRecord.line_item_type == "usage")
            .group_by(col)
            .order_by(func.sum(CanonicalCostRecord.provider_billed).desc())
            .limit(50))
    if period_start:
        stmt = stmt.where(CanonicalCostRecord.billing_period_start == period_start)
    rows = (await session.execute(stmt)).all()
    return {"group_by": group_by,
            "items": [{"group": str(g), "cost": str(a or 0)} for g, a in rows]}


@router.get("/portal/invoices")
async def portal_invoices(session: SessionDep, principal: CustomerPrincipal):
    cust = await _my_customer(session, principal)
    rows = (await session.execute(
        select(Invoice).where(Invoice.customer_id == cust.id,
                              Invoice.deleted_at.is_(None),
                              Invoice.status.in_(("issued", "exported", "paid_or_settled",
                                                  "disputed", "corrected")))
        .order_by(Invoice.period_start.desc()))).scalars().all()
    return [{
        "id": str(i.id), "invoice_number": i.invoice_number,
        "total": str(i.total), "currency": i.currency,
        "period_start": i.period_start.isoformat(), "period_end": i.period_end.isoformat(),
        "status": i.status, "issued_at": i.issued_at.isoformat() if i.issued_at else None,
        "due_date": i.due_date.isoformat() if i.due_date else None,
        "notes_customer": i.notes_customer,
    } for i in rows]


@router.get("/portal/invoices/{invoice_id}")
async def portal_invoice_detail(invoice_id: uuid.UUID, session: SessionDep,
                                principal: CustomerPrincipal):
    cust = await _my_customer(session, principal)
    inv = await session.get(Invoice, invoice_id)
    if inv is None or inv.customer_id != cust.id or inv.status not in (
        "issued", "exported", "paid_or_settled", "disputed", "corrected"):
        raise HTTPException(404, detail={"code": "not_found"})
    lines = (await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id,
                                  InvoiceLine.customer_visible.is_(True))
        .order_by(InvoiceLine.line_number))).scalars().all()
    return {
        "id": str(inv.id), "invoice_number": inv.invoice_number,
        "customer_name": cust.display_name,
        "currency": inv.currency, "payment_terms": inv.payment_terms,
        "period_start": inv.period_start.isoformat(), "period_end": inv.period_end.isoformat(),
        "subtotal": str(inv.subtotal), "discounts_total": str(inv.discounts_total),
        "credits_total": str(inv.credits_total), "fees_total": str(inv.fees_total),
        "adjustments_total": str(inv.adjustments_total), "taxes_total": str(inv.taxes_total),
        "prior_period_adjustments_total": str(inv.prior_period_adjustments_total),
        "total": str(inv.total), "status": inv.status, "notes_customer": inv.notes_customer,
        "lines": [{"line_number": ln.line_number, "kind": ln.kind,
                   "description": ln.description,
                   "quantity": str(ln.quantity) if ln.quantity is not None else None,
                   "amount": str(ln.amount),
                   "source_record_count": len(ln.source_record_ids)} for ln in lines],
    }
