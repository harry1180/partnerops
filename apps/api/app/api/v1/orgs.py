"""Organizations: tree for current scope, create sub-orgs, customers as org nodes.

Cross-tenant probing returns 404 (no existence leakage). Creation is limited
to platform_admin, or to one's own subtree by distributor/msp admins with
`org.manage`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.billing_core import Customer
from app.models.org import KINDS, Organization
from app.services.audit_service import record_audit

router = APIRouter()


class OrgCreate(BaseModel):
    kind: str = Field(pattern="^(" + "|".join(KINDS) + ")$")
    name: str = Field(min_length=2, max_length=255)
    legal_name: str | None = None
    parent_id: uuid.UUID | None = None
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")


class OrgOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    legal_name: str | None
    parent_id: uuid.UUID | None
    path: str
    currency: str
    status: str
    child_count: int | None = None


@router.get("", response_model=list[OrgOut])
async def list_org_tree(session: SessionDep, principal: Principal):
    """Return all organizations visible to the caller (RLS-limited for orgs
    table via explicit scope filter — organizations themselves are protected
    by the subtree predicate below)."""
    stmt = select(Organization).where(
        Organization.deleted_at.is_(None),
        Organization.status != "archived",
    )
    if not principal.is_platform_admin:
        root = principal.scope_prefixes[0]
        # org paths end with "/" — the subtree predicate is prefix-match.
        stmt = stmt.where(
            (Organization.path == root) | (Organization.path.like(root + "%"))
        )
    orgs = list((await session.execute(stmt.order_by(Organization.path))).scalars())
    counts: dict[uuid.UUID, int] = {}
    for o in orgs:
        if o.parent_id:
            counts[o.parent_id] = counts.get(o.parent_id, 0) + 1
    return [
        OrgOut(
            id=o.id, kind=o.kind, name=o.name, legal_name=o.legal_name,
            parent_id=o.parent_id, path=o.path, currency=o.currency,
            status=o.status, child_count=counts.get(o.id, 0),
        )
        for o in orgs
    ]


@router.post("", response_model=OrgOut, status_code=201, dependencies=[CSRF])
async def create_org(body: OrgCreate, session: SessionDep, principal: Principal):
    parent_id = body.parent_id
    if parent_id is None:
        if not principal.is_platform_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "platform_admin_required"})
        parent = await session.get(Organization, principal.org_id)
    else:
        parent = await session.get(Organization, parent_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
        if not principal.is_platform_admin:
            root = principal.scope_prefixes[0]
            if not parent.path.startswith(root):
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
            if not principal.can("org.manage"):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
            allowed_children = {
                "platform": ("distributor",),
                "distributor": ("reseller",),
                "reseller": ("customer",),
            }
            if body.kind not in allowed_children.get(parent.kind, ()):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_hierarchy",
                        "message": f"{parent.kind} cannot contain {body.kind}",
                    },
                )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "parent_not_found"})

    new_id = uuid.uuid4()
    org = Organization(
        id=new_id,
        kind=body.kind,
        name=body.name.strip(),
        legal_name=body.legal_name,
        parent_id=parent.id,
        path=f"{parent.path}{str(new_id)}/",
        currency=body.currency,
    )
    await set_org_scope(session, org.path)
    session.add(org)
    await record_audit(
        session, principal, action="org.created", org_path=org.path,
        summary=f"Created {body.kind} '{body.name}'", entity_type="organization", entity_id=new_id,
        detail={"kind": body.kind, "parent": str(parent.id)},
    )
    await session.commit()
    return OrgOut(
        id=org.id, kind=org.kind, name=org.name, legal_name=org.legal_name,
        parent_id=org.parent_id, path=org.path, currency=org.currency, status=org.status,
        child_count=0,
    )


@router.post("/{org_id}/customers", response_model=dict, status_code=201, dependencies=[CSRF])
async def create_customer(org_id: uuid.UUID, body: dict, session: SessionDep, principal: Principal):
    """Create a customer record under a partner org; optionally a matching
    customer organization node for portal users."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    parent = await session.get(Organization, org_id)
    if parent is None or not _in_scope(parent, principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    name = str(body.get("name", "")).strip()
    code = str(body.get("code", "")).strip()
    if len(name) < 2 or len(code) < 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "validation_error", "message": "name and code are required"},
        )
    cust_org: Organization | None = None
    if body.get("create_org", True):
        cid = uuid.uuid4()
        cust_org = Organization(
            id=cid, kind="customer", name=name, parent_id=parent.id,
            path=f"{parent.path}{str(cid)}/", currency=parent.currency,
        )
        session.add(cust_org)
        await session.flush()
    org_path = cust_org.path if cust_org else parent.path
    customer = Customer(
        org_id=parent.id,
        org_path=org_path,
        code=code,
        display_name=name,
        billing_email=body.get("billing_email"),
        po_number=body.get("po_number"),
        billing_address=body.get("billing_address") or {},
    )
    await set_org_scope(session, parent.path)
    session.add(customer)
    await record_audit(
        session, principal, action="customer.created", org_path=parent.path,
        summary=f"Created customer '{name}' ({code})", entity_type="customer", entity_id=customer.id,
    )
    await session.commit()
    return {
        "id": str(customer.id), "code": customer.code, "name": customer.display_name,
        "org_id": str(cust_org.id) if cust_org else None,
    }


def _in_scope(org: Organization, principal: Principal) -> bool:
    if principal.is_platform_admin:
        return True
    root = principal.scope_prefixes[0]
    return org.path.startswith(root) or root.startswith(org.path)
