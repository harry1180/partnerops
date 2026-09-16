"""Customers + account families API (Phase 0/1 core).

RLS-bound queries only: even if a WHERE is forgotten, the policy returns
nothing outside the caller's subtree. Cross-tenant ids 404 — never 403 with
existence hints on private records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.billing_core import AccountFamily, CloudAccount, Customer
from app.services.audit_service import record_audit

router = APIRouter()


class CustomerOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    org_path: str
    owning_org_id: uuid.UUID
    account_family_count: int | None = None
    target_margin_pct: float | None = None


class CustomerPage(BaseModel):
    items: list[CustomerOut]
    total: int
    page: int
    page_size: int


class AccountFamilyOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    name: str
    description: str | None
    account_count: int | None = None


class AccountFamilyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None


def _in_customer_scope(org_path: str, principal: Principal) -> bool:
    root = principal.scope_prefixes[0]
    return org_path.startswith(root) or root.startswith(org_path)


async def _load_scoped_customer(
    session: SessionDep, principal: Principal, customer_id: uuid.UUID
) -> Customer:
    c = await session.get(Customer, customer_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    if not principal.is_platform_admin and not _in_customer_scope(c.org_path, principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    return c


async def _family_counts(session: SessionDep, customer_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AccountFamily.id)).where(
                    AccountFamily.customer_id == customer_id,
                    AccountFamily.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


def _to_out(c: Customer, family_count: int | None = None) -> CustomerOut:
    return CustomerOut(
        id=c.id,
        code=c.code,
        name=c.display_name,
        status="active",
        org_path=c.org_path,
        owning_org_id=c.org_id,
        account_family_count=family_count,
        target_margin_pct=float(c.target_margin_pct) if c.target_margin_pct is not None else None,
    )


@router.get("", response_model=CustomerPage)
async def list_customers(
    session: SessionDep,
    principal: Principal,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    stmt = select(Customer).where(Customer.deleted_at.is_(None))
    count_stmt = select(func.count(Customer.id)).where(Customer.deleted_at.is_(None))
    if not principal.is_platform_admin:
        root = principal.scope_prefixes[0]
        stmt = stmt.where(Customer.org_path.like(root + "%"))
        count_stmt = count_stmt.where(Customer.org_path.like(root + "%"))
    if search:
        like = f"%{search.strip()}%"
        cond = Customer.display_name.ilike(like) | Customer.code.ilike(like)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = int((await session.execute(count_stmt)).scalar_one())
    page_stmt = stmt.order_by(Customer.display_name).offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(page_stmt)).scalars()
    items = [_to_out(c, await _family_counts(session, c.id)) for c in rows]
    return CustomerPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: uuid.UUID, session: SessionDep, principal: Principal):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    c = await _load_scoped_customer(session, principal, customer_id)
    return _to_out(c, await _family_counts(session, c.id))


@router.get("/{customer_id}/account-families", response_model=list[AccountFamilyOut])
async def list_families(customer_id: uuid.UUID, session: SessionDep, principal: Principal):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    c = await _load_scoped_customer(session, principal, customer_id)
    fams = (
        await session.execute(
            select(AccountFamily)
            .where(AccountFamily.customer_id == c.id, AccountFamily.deleted_at.is_(None))
            .order_by(AccountFamily.name)
        )
    ).scalars()
    out = []
    for f in fams:
        n = (
            await session.execute(
                select(func.count(CloudAccount.id)).where(CloudAccount.account_family_id == f.id)
            )
        ).scalar_one()
        out.append(
            AccountFamilyOut(
                id=f.id,
                customer_id=f.customer_id,
                name=f.name,
                description=f.description,
                account_count=int(n),
            )
        )
    return out


@router.post(
    "/{customer_id}/account-families",
    response_model=AccountFamilyOut,
    status_code=201,
    dependencies=[CSRF],
)
async def create_family(
    customer_id: uuid.UUID,
    body: AccountFamilyCreate,
    session: SessionDep,
    principal: Principal,
):
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    c = await _load_scoped_customer(session, principal, customer_id)
    await set_org_scope(session, c.org_path)
    fam = AccountFamily(
        customer_id=c.id,
        name=body.name.strip(),
        description=body.description,
        org_id=c.org_id,
        org_path=c.org_path,
    )
    session.add(fam)
    await record_audit(
        session,
        principal,
        action="account_family.created",
        org_path=c.org_path,
        summary=f"Account family '{body.name}' for customer '{c.display_name}'",
        entity_type="account_family",
        entity_id=fam.id,
    )
    await session.commit()
    return AccountFamilyOut(
        id=fam.id, customer_id=fam.customer_id, name=fam.name, description=fam.description
    )


@router.patch("/{customer_id}", response_model=dict, dependencies=[CSRF])
async def update_customer(
    customer_id: uuid.UUID,
    body: dict,
    session: SessionDep,
    principal: Principal,
):
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    c = await _load_scoped_customer(session, principal, customer_id)
    allowed = {"display_name", "code", "billing_email", "po_number", "target_margin_pct"}
    changed: dict = {}
    for k, v in body.items():
        if k not in allowed:
            continue
        if k == "target_margin_pct" and v is not None:
            v = float(v)
        setattr(c, k, v)
        changed[k] = v
    await record_audit(
        session,
        principal,
        action="customer.updated",
        org_path=c.org_path,
        summary=f"Customer '{c.display_name}' updated: {sorted(changed)}",
        entity_type="customer",
        entity_id=c.id,
        detail={"changed": sorted(changed)},
    )
    await session.commit()
    return {"ok": True, "id": str(c.id)}


@router.delete("/{customer_id}", response_model=dict, dependencies=[CSRF])
async def delete_customer(customer_id: uuid.UUID, session: SessionDep, principal: Principal):
    """Soft delete only — business records are never hard-deleted."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    c = await _load_scoped_customer(session, principal, customer_id)
    c.deleted_at = datetime.now(UTC)
    await record_audit(
        session,
        principal,
        action="customer.deleted",
        org_path=c.org_path,
        summary=f"Customer '{c.display_name}' archived (soft delete)",
        entity_type="customer",
        entity_id=c.id,
    )
    await session.commit()
    return {"ok": True}
