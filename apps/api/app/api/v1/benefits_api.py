"""Credits, commitments, and allocation policies (Phase 2).

Track-and-allocate only: the platform never purchases or modifies provider
commitments (charter). Coverage/utilization are computed from canonical cost
records — real numbers, no estimates presented as facts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.engine.money import ZERO, q
from app.models.benefits import (
    COMMITMENT_KINDS,
    CREDIT_KINDS,
    SHARING_MODES,
    AllocationPolicy,
    Commitment,
    Credit,
)
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord
from app.services.audit_service import record_audit

router = APIRouter()


def _root(principal: Principal) -> str:
    return principal.scope_prefixes[0]


# --------------------------------------------------------------------- credits

class CreditCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=255)
    kind: str = Field(default="service", pattern=f"^({'|'.join(CREDIT_KINDS)})$")
    provider_code: str = Field(default="aws", max_length=32)
    external_id: str | None = Field(default=None, max_length=128)
    customer_id: uuid.UUID | None = None
    amount_total: str
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    effective_start: datetime | None = None
    expires_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/credits")
async def list_credits(session: SessionDep, principal: Principal,
                      allocation_status: str | None = Query(
                          None, pattern="^(unallocated|partially_allocated|allocated|expired)$")):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    conds: list = [Credit.org_path.like(root + "%"), Credit.deleted_at.is_(None)]
    if allocation_status:
        conds.append(Credit.allocation_status == allocation_status)
    rows = (await session.execute(
        select(Credit, Customer.display_name)
        .outerjoin(Customer, Customer.id == Credit.customer_id)
        .where(*conds).order_by(Credit.created_at.desc()).limit(200))).all()
    return [{
        "id": str(c.id), "display_name": c.display_name, "kind": c.kind,
        "provider": c.provider_code, "customer": cust_name,
        "amount_total": str(c.amount_total), "amount_used": str(c.amount_used),
        "remaining": str(q((c.amount_total or ZERO) - (c.amount_used or ZERO))),
        "currency": c.currency, "allocation_status": c.allocation_status,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "notes": c.notes,
    } for c, cust_name in rows]


@router.post("/credits", status_code=201, dependencies=[CSRF])
async def create_credit(body: CreditCreate, session: SessionDep, principal: Principal):
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    try:
        amount = Decimal(body.amount_total)
    except Exception:
        raise HTTPException(400, detail={"code": "bad_amount"}) from None
    if amount <= ZERO:
        raise HTTPException(400, detail={"code": "amount_must_be_positive"})
    org_id = principal.org_id
    org_path = root
    if body.customer_id:
        cust = await session.get(Customer, body.customer_id)
        if cust is None or not cust.org_path.startswith(root):
            raise HTTPException(404, detail={"code": "customer_not_found"})
        org_id, org_path = cust.org_id, cust.org_path
    await set_org_scope(session, org_path)
    c = Credit(
        kind=body.kind, provider_code=body.provider_code, external_id=body.external_id,
        display_name=body.display_name, customer_id=body.customer_id,
        amount_total=q(amount), amount_used=ZERO, currency=body.currency,
        effective_start=body.effective_start, expires_at=body.expires_at,
        notes=body.notes, org_id=org_id, org_path=org_path,
    )
    session.add(c)
    await record_audit(session, principal, action="credit.created", org_path=org_path,
                       summary=f"Credit '{body.display_name}' {amount} {body.currency}",
                       entity_type="credit", entity_id=c.id)
    await session.commit()
    return {"id": str(c.id), "allocation_status": c.allocation_status}


class CreditAllocate(BaseModel):
    customer_id: uuid.UUID
    amount: str


@router.post("/credits/{credit_id}/allocate", dependencies=[CSRF])
async def allocate_credit(credit_id: uuid.UUID, body: CreditAllocate,
                          session: SessionDep, principal: Principal):
    """Attribute an unallocated credit to a customer (their next pricing run
    can then apply it per contract policy)."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    c = await session.get(Credit, credit_id)
    if c is None or not c.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    cust = await session.get(Customer, body.customer_id)
    if cust is None or not cust.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "customer_not_found"})
    try:
        amt = Decimal(body.amount)
    except Exception:
        raise HTTPException(400, detail={"code": "bad_amount"}) from None
    remaining = q((c.amount_total or ZERO) - (c.amount_used or ZERO))
    if amt <= ZERO or amt > remaining:
        raise HTTPException(400, detail={"code": "over_allocation",
                                         "message": f"remaining is {remaining} {c.currency}"})
    await set_org_scope(session, c.org_path)
    c.customer_id = cust.id
    c.amount_used = q((c.amount_used or ZERO) + amt)
    c.allocation_status = "allocated" if c.amount_used >= q(c.amount_total) else "partially_allocated"
    await record_audit(session, principal, action="credit.allocated", org_path=c.org_path,
                       summary=f"Allocated {amt} {c.currency} of '{c.display_name}' to {cust.display_name}",
                       entity_type="credit", entity_id=c.id)
    await session.commit()
    return {"ok": True, "allocation_status": c.allocation_status,
            "remaining": str(q(c.amount_total - c.amount_used))}


# ---------------------------------------------------------------- commitments

class CommitmentCreate(BaseModel):
    kind: str = Field(pattern=f"^({'|'.join(COMMITMENT_KINDS)})$")
    display_name: str = Field(min_length=2, max_length=255)
    external_id: str | None = Field(default=None, max_length=128)
    provider_code: str = Field(default="aws", max_length=32)
    customer_id: uuid.UUID | None = None
    start_date: datetime
    end_date: datetime | None = None
    hourly_commitment: str | None = None
    upfront_paid: str | None = None
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    coverage_scope: dict = Field(default_factory=dict)


@router.get("/commitments")
async def list_commitments(session: SessionDep, principal: Principal):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    rows = (await session.execute(
        select(Commitment, Customer.display_name)
        .outerjoin(Customer, Customer.id == Commitment.customer_id)
        .where(Commitment.org_path.like(root + "%"), Commitment.deleted_at.is_(None))
        .order_by(Commitment.start_date.desc()).limit(200))).all()
    return [{
        "id": str(x.id), "kind": x.kind, "display_name": x.display_name,
        "external_id": x.external_id, "provider": x.provider_code,
        "customer": cust_name, "start_date": x.start_date.isoformat(),
        "end_date": x.end_date.isoformat() if x.end_date else None,
        "hourly_commitment": str(x.hourly_commitment) if x.hourly_commitment is not None else None,
        "currency": x.currency, "status": x.status, "coverage_scope": x.coverage_scope,
    } for x, cust_name in rows]


@router.post("/commitments", status_code=201, dependencies=[CSRF])
async def create_commitment(body: CommitmentCreate, session: SessionDep,
                            principal: Principal):
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    org_id, org_path = principal.org_id, root
    if body.customer_id:
        cust = await session.get(Customer, body.customer_id)
        if cust is None or not cust.org_path.startswith(root):
            raise HTTPException(404, detail={"code": "customer_not_found"})
        org_id, org_path = cust.org_id, cust.org_path
    await set_org_scope(session, org_path)
    x = Commitment(
        kind=body.kind, external_id=body.external_id, display_name=body.display_name,
        provider_code=body.provider_code, customer_id=body.customer_id,
        start_date=body.start_date, end_date=body.end_date,
        hourly_commitment=Decimal(body.hourly_commitment) if body.hourly_commitment else None,
        upfront_paid=Decimal(body.upfront_paid) if body.upfront_paid else None,
        currency=body.currency, coverage_scope=body.coverage_scope,
        org_id=org_id, org_path=org_path,
    )
    session.add(x)
    await record_audit(session, principal, action="commitment.created", org_path=org_path,
                       summary=f"Commitment '{body.display_name}' ({body.kind}) tracked",
                       entity_type="commitment", entity_id=x.id)
    await session.commit()
    return {"id": str(x.id)}


@router.get("/commitments/coverage")
async def commitment_coverage(session: SessionDep, principal: Principal,
                              period_start: datetime, period_end: datetime):
    """Coverage & utilization computed from canonical records for a period:
    which usage was covered by commitment pricing (ondemand-equivalent above
    provider-billed ⇒ discount applied) and the discount captured.
    Deterministic SQL — no estimates."""
    if not principal.can("cost.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    base = [CanonicalCostRecord.billing_period_start == period_start,
            CanonicalCostRecord.billing_period_end == period_end,
            CanonicalCostRecord.org_path.like(root + "%"),
            CanonicalCostRecord.line_item_type == "usage"]
    total_ondemand = Decimal(str((await session.execute(
        select(func.coalesce(func.sum(CanonicalCostRecord.ondemand_equivalent), 0)).where(*base)
    )).scalar_one() or 0))
    total_billed = Decimal(str((await session.execute(
        select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0)).where(*base)
    )).scalar_one() or 0))
    # covered = rows where provider billed strictly below on-demand equivalent
    covered = Decimal(str((await session.execute(
        select(func.coalesce(func.sum(CanonicalCostRecord.ondemand_equivalent), 0)).where(
            *base, CanonicalCostRecord.provider_billed < CanonicalCostRecord.ondemand_equivalent)
    )).scalar_one() or 0))
    savings = q(total_ondemand - total_billed)
    return {
        "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "ondemand_equivalent": str(q(total_ondemand)),
        "provider_billed": str(q(total_billed)),
        "covered_ondemand_equivalent": str(q(covered)),
        "coverage_pct": str(q(covered / total_ondemand * 100)) if total_ondemand else None,
        "commitment_savings": str(savings),
    }


# ------------------------------------------------------- allocation policies

class PolicyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    benefit_class: str = Field(pattern="^(commitment|credit|discount|support|marketplace)$")
    mode: str = Field(pattern=f"^({'|'.join(SHARING_MODES)})$")
    share_pct: str | None = None
    parameters: dict = Field(default_factory=dict)
    priority: int = 100


@router.get("/allocation-policies")
async def list_policies(session: SessionDep, principal: Principal):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    rows = (await session.execute(
        select(AllocationPolicy).where(AllocationPolicy.org_path.like(root + "%"),
                                       AllocationPolicy.deleted_at.is_(None))
        .order_by(AllocationPolicy.priority))).scalars()
    return [{
        "id": str(x.id), "name": x.name, "benefit_class": x.benefit_class,
        "mode": x.mode, "share_pct": str(x.share_pct) if x.share_pct is not None else None,
        "parameters": x.parameters, "priority": x.priority,
    } for x in rows]


@router.post("/allocation-policies", status_code=201, dependencies=[CSRF])
async def create_policy(body: PolicyCreate, session: SessionDep, principal: Principal):
    if not principal.can("contract.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = _root(principal)
    await set_org_scope(session, root)
    x = AllocationPolicy(
        name=body.name, benefit_class=body.benefit_class, mode=body.mode,
        share_pct=Decimal(body.share_pct) if body.share_pct else None,
        parameters=body.parameters, priority=body.priority,
        org_id=principal.org_id, org_path=root,
    )
    session.add(x)
    await record_audit(session, principal, action="allocation_policy.created", org_path=root,
                       summary=f"Allocation policy '{body.name}' ({body.benefit_class} → {body.mode})",
                       entity_type="allocation_policy", entity_id=x.id)
    await session.commit()
    return {"id": str(x.id)}
