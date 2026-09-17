"""Contracts + billing rules API.

Rules of the domain (charter):
- editing a contract that has been used ⇒ ALWAYS create a new version
- active version overlap ⇒ 409 unless overlap_approved_by a user with
  contract.approve is set
- rules: draft → (sandbox test) → pending_approval (if high impact) →
  published; publish needs rule.publish, high-impact additionally needs an
  approved Approval whose checker != maker
- contract versions pin rule bindings (list of {rule_id, rule_version_id,
  order}); published rule versions are immutable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.approvals import ApprovalRequest
from app.models.billing_core import Customer
from app.models.contracts import (
    CADENCES,
    PRICING_BASES,
    RULE_TYPES,
    BillingRule,
    BillingRuleVersion,
    Contract,
    ContractVersion,
)
from app.services.audit_service import record_audit

router = APIRouter()


class ContractCreate(BaseModel):
    customer_id: uuid.UUID
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    effective_start: datetime
    effective_end: datetime | None = None
    currency: str = "USD"
    billing_cadence: str = "monthly"
    payment_terms: str = "Net 30"
    pricing_basis: str = "provider_billed"
    minimum_monthly: str | None = None
    rounding_rule: dict = Field(default_factory=dict)
    support_fee_policy: dict = Field(default_factory=lambda: {"mode": "pass_through"})
    tax_behavior: dict = Field(default_factory=lambda: {"mode": "pass_through"})
    discount_policy: dict = Field(default_factory=dict)
    credit_sharing_policy: dict = Field(default_factory=dict)
    commitment_sharing_policy: dict = Field(default_factory=dict)
    managed_service_fee: dict = Field(default_factory=dict)
    invoice_grouping: list[str] = Field(default_factory=lambda: ["service"])
    notes: str | None = None


class NewVersion(BaseModel):
    changes: dict


class RuleCreate(BaseModel):
    contract_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    rule_type: str


class RuleVersionCreate(BaseModel):
    priority: int = 100
    calc_order: int = 100
    applied_basis: str = "running_total"
    filters: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)
    effective_start: datetime | None = None
    effective_end: datetime | None = None
    high_impact: bool = False
    notes: str | None = None


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


async def _load_customer(session: SessionDep, principal: Principal, customer_id: uuid.UUID) -> Customer:
    c = await session.get(Customer, customer_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not c.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    return c


@router.get("/contracts")
async def list_contracts(session: SessionDep, principal: Principal,
                        customer_id: uuid.UUID | None = None):
    if not principal.can("contract.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    stmt = select(Contract).where(Contract.deleted_at.is_(None))
    if customer_id:
        stmt = stmt.where(Contract.customer_id == customer_id)
    rows = (await session.execute(stmt.order_by(Contract.name))).scalars().all()
    out = []
    for c in rows:
        versions = (
            await session.execute(
                select(ContractVersion).where(ContractVersion.contract_id == c.id)
                .order_by(ContractVersion.version_number.desc())
            )
        ).scalars().all()
        out.append({
            "id": str(c.id), "code": c.code, "name": c.name, "status": c.status,
            "customer_id": str(c.customer_id),
            "versions": [{
                "id": str(v.id), "version_number": v.version_number, "status": v.status,
                "effective_start": v.effective_start.isoformat(),
                "effective_end": v.effective_end.isoformat() if v.effective_end else None,
                "currency": v.currency, "pricing_basis": v.pricing_basis,
                "rule_bindings": v.rule_bindings,
            } for v in versions],
        })
    return out


@router.post("/contracts", status_code=201, dependencies=[CSRF])
async def create_contract(body: ContractCreate, session: SessionDep, principal: Principal):
    if not principal.can("contract.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    cust = await _load_customer(session, principal, body.customer_id)
    await set_org_scope(session, cust.org_path)
    if body.billing_cadence not in CADENCES:
        raise HTTPException(400, detail={"code": "bad_cadence"})
    if body.pricing_basis not in PRICING_BASES:
        raise HTTPException(400, detail={"code": "bad_pricing_basis"})
    contract = Contract(
        customer_id=cust.id, code=body.code, name=body.name, notes=body.notes,
        org_id=cust.org_id, org_path=cust.org_path, status="draft",
    )
    session.add(contract)
    await session.flush()
    cv = ContractVersion(
        contract_id=contract.id, version_number=1, status="draft",
        effective_start=body.effective_start, effective_end=body.effective_end,
        currency=body.currency, billing_cadence=body.billing_cadence,
        payment_terms=body.payment_terms, pricing_basis=body.pricing_basis,
        minimum_monthly=_dec(body.minimum_monthly),
        rounding_rule=body.rounding_rule or {"mode": "half_up", "level": "line", "increment": "0.01"},
        support_fee_policy=body.support_fee_policy, tax_behavior=body.tax_behavior,
        discount_policy=body.discount_policy, credit_sharing_policy=body.credit_sharing_policy,
        commitment_sharing_policy=body.commitment_sharing_policy,
        managed_service_fee=body.managed_service_fee, invoice_grouping=body.invoice_grouping,
        org_id=cust.org_id, org_path=cust.org_path, created_by=principal.user_id,
    )
    session.add(cv)
    await record_audit(session, principal, action="contract.created", org_path=cust.org_path,
                       summary=f"Contract '{body.name}' v1 created for {cust.display_name}",
                       entity_type="contract", entity_id=contract.id)
    await session.commit()
    return {"id": str(contract.id), "version_id": str(cv.id)}


@router.post("/contracts/{contract_id}/versions", status_code=201, dependencies=[CSRF])
async def add_contract_version(contract_id: uuid.UUID, body: NewVersion,
                               session: SessionDep, principal: Principal):
    if not principal.can("contract.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    contract = await session.get(Contract, contract_id)
    if contract is None or contract.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not contract.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    latest = (
        await session.execute(
            select(func.max(ContractVersion.version_number)).where(
                ContractVersion.contract_id == contract.id)
        )
    ).scalar_one()
    payload = dict(body.changes)
    await set_org_scope(session, contract.org_path)
    cv = ContractVersion(
        contract_id=contract.id, version_number=int(latest or 0) + 1, status="draft",
        effective_start=datetime.fromisoformat(payload.pop("effective_start")),
effective_end=datetime.fromisoformat(payload["effective_end"])
                if payload.get("effective_end") else None,
        org_id=contract.org_id, org_path=contract.org_path,
        created_by=principal.user_id,
    )
    for k, v in payload.items():
        if hasattr(cv, k) and k not in ("effective_start", "effective_end"):
            setattr(cv, k, v)
    session.add(cv)
    await record_audit(session, principal, action="contract.version_created",
                       org_path=contract.org_path,
                       summary=f"Contract {contract.code}: version {cv.version_number} created",
                       entity_type="contract_version", entity_id=cv.id,
                       detail={"changed": sorted(body.changes)})
    await session.commit()
    return {"id": str(cv.id), "version_number": cv.version_number}


@router.patch("/contract-versions/{version_id}/bindings", dependencies=[CSRF])
async def set_rule_bindings(version_id: uuid.UUID, body: dict, session: SessionDep,
                           principal: Principal):
    """Pin rule versions on a DRAFT contract version: [{rule_id, rule_version_id, order}].
    Once a version is active/used, changes must go through /versions (new version)."""
    if not principal.can("contract.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    cv = await session.get(ContractVersion, version_id)
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not cv.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    if cv.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"code": "version_not_draft",
                                    "message": "create a new version to change bindings"})
    bindings = body.get("rule_bindings")
    if not isinstance(bindings, list):
        raise HTTPException(400, detail={"code": "bad_bindings"})
    for b in bindings:
        rv = await session.get(BillingRuleVersion, uuid.UUID(str(b.get("rule_version_id", ""))))
        if rv is None or rv.status not in ("published", "draft"):
            raise HTTPException(400, detail={"code": "unknown_rule_version",
                                             "rule_version_id": b.get("rule_version_id")})
    cv.rule_bindings = [
        {"rule_id": str(uuid.UUID(str(b["rule_id"]))),
         "rule_version_id": str(uuid.UUID(str(b["rule_version_id"]))),
         "order": int(b.get("order", i))}
        for i, b in enumerate(bindings)
    ]
    await record_audit(session, principal, action="contract.version_created",
                       org_path=cv.org_path,
                       summary=f"Rule bindings set on draft version {cv.version_number} "
                               f"({len(bindings)} rules)",
                       entity_type="contract_version", entity_id=cv.id)
    await session.commit()
    return {"ok": True, "rule_bindings": cv.rule_bindings}


@router.post("/contract-versions/{version_id}/activate", dependencies=[CSRF])
async def activate_contract_version(version_id: uuid.UUID, session: SessionDep,
                                    principal: Principal):
    """Active date-overlap guard: 409 unless explicitly approved overlap."""
    if not principal.can("contract.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    cv = await session.get(ContractVersion, version_id)
    if cv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not cv.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    await set_org_scope(session, cv.org_path)

    overlapping = (
        await session.execute(
            select(ContractVersion).join(Contract, Contract.id == ContractVersion.contract_id)
            .where(
                ContractVersion.status == "active",
                Contract.id == cv.contract_id,
                ContractVersion.id != cv.id,
                ContractVersion.effective_start < (cv.effective_end or datetime(2999, 1, 1, tzinfo=UTC)),
                (ContractVersion.effective_end.is_(None))
                | (ContractVersion.effective_end > cv.effective_start),
            )
        )
    ).scalars().all()
    if overlapping and cv.overlap_approved_by is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={
            "code": "overlap_requires_approval",
            "overlapping_version_ids": [str(o.id) for o in overlapping],
        })

    for o in overlapping:
        o.status = "superseded"
    cv.status = "active"
    cv.approved_by = principal.user_id
    cv.approved_at = datetime.now(UTC)
    contract = await session.get(Contract, cv.contract_id)
    if contract:
        contract.status = "active"
    await record_audit(session, principal, action="contract.version_created",
                       org_path=cv.org_path,
                       summary=f"Contract version {cv.version_number} activated"
                               + (" with overlap approval" if overlapping else ""),
                       entity_type="contract_version", entity_id=cv.id)
    await session.commit()
    return {"ok": True, "status": cv.status}


# ------------------------------------------------------------------ rules

@router.get("/billing-rules")
async def list_rules(session: SessionDep, principal: Principal,
                     contract_id: uuid.UUID | None = None):
    if not principal.can("contract.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    stmt = select(BillingRule).where(BillingRule.deleted_at.is_(None))
    if contract_id:
        stmt = stmt.where(BillingRule.contract_id == contract_id)
    rules = (await session.execute(stmt.order_by(BillingRule.code))).scalars().all()
    out = []
    for rule in rules:
        versions = (
            await session.execute(
                select(BillingRuleVersion)
                .where(BillingRuleVersion.rule_id == rule.id)
                .order_by(BillingRuleVersion.version_number.desc())
            )
        ).scalars().all()
        out.append({
            "id": str(rule.id), "code": rule.code, "name": rule.name,
            "rule_type": rule.rule_type, "status": rule.status,
            "contract_id": str(rule.contract_id) if rule.contract_id else None,
            "versions": [{
                "id": str(v.id), "version_number": v.version_number, "status": v.status,
                "priority": v.priority, "calc_order": v.calc_order,
                "applied_basis": v.applied_basis, "filters": v.filters,
                "parameters": v.parameters, "high_impact": v.high_impact,
                "estimated_impact_monthly": str(v.estimated_impact_monthly)
                if v.estimated_impact_monthly is not None else None,
            } for v in versions],
        })
    return out


@router.post("/billing-rules", status_code=201, dependencies=[CSRF])
async def create_rule(body: RuleCreate, session: SessionDep, principal: Principal):
    if not principal.can("rule.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    if body.rule_type not in RULE_TYPES:
        raise HTTPException(400, detail={"code": "bad_rule_type", "allowed": list(RULE_TYPES)})
    org_path = principal.org_path
    org_id = principal.org_id
    if body.customer_id:
        cust = await _load_customer(session, principal, body.customer_id)
        org_path, org_id = cust.org_path, cust.org_id
    elif body.contract_id:
        contract = await session.get(Contract, body.contract_id)
        if contract is None:
            raise HTTPException(404, detail={"code": "not_found"})
        org_path, org_id = contract.org_path, contract.org_id
    await set_org_scope(session, org_path)
    rule = BillingRule(
        contract_id=body.contract_id, customer_id=body.customer_id,
        code=body.code, name=body.name, rule_type=body.rule_type,
        org_id=org_id, org_path=org_path,
    )
    session.add(rule)
    await session.flush()
    v = BillingRuleVersion(
        rule_id=rule.id, version_number=1, status="draft", priority=100,
        calc_order=100, applied_basis="running_total",
        org_id=org_id, org_path=org_path, created_by=principal.user_id,
    )
    session.add(v)
    await record_audit(session, principal, action="billing_rule.created", org_path=org_path,
                       summary=f"Billing rule '{body.code}' ({body.rule_type}) created",
                       entity_type="billing_rule", entity_id=rule.id)
    await session.commit()
    return {"id": str(rule.id), "version_id": str(v.id)}


@router.post("/billing-rules/{rule_id}/versions", status_code=201, dependencies=[CSRF])
async def add_rule_version(rule_id: uuid.UUID, body: RuleVersionCreate,
                           session: SessionDep, principal: Principal):
    if not principal.can("rule.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    rule = await session.get(BillingRule, rule_id)
    if rule is None or rule.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    await set_org_scope(session, rule.org_path)
    latest = (
        await session.execute(
            select(func.max(BillingRuleVersion.version_number)).where(
                BillingRuleVersion.rule_id == rule.id)
        )
    ).scalar_one()
    v = BillingRuleVersion(
        rule_id=rule.id, version_number=int(latest or 0) + 1, status="draft",
        priority=body.priority, calc_order=body.calc_order,
        applied_basis=body.applied_basis, filters=body.filters,
        parameters=body.parameters | {"type": rule.rule_type},
        effective_start=body.effective_start, effective_end=body.effective_end,
        high_impact=body.high_impact, notes=body.notes,
        org_id=rule.org_id, org_path=rule.org_path, created_by=principal.user_id,
    )
    session.add(v)
    await record_audit(session, principal, action="billing_rule.version_created",
                       org_path=rule.org_path,
                       summary=f"Rule '{rule.code}': version {v.version_number} drafted",
                       entity_type="billing_rule_version", entity_id=v.id)
    await session.commit()
    return {"id": str(v.id), "version_number": v.version_number}


@router.post("/billing-rule-versions/{version_id}/publish", dependencies=[CSRF])
async def publish_rule_version(version_id: uuid.UUID, session: SessionDep,
                               principal: Principal):
    """Publish gates: rule.publish; high-impact additionally requires an
    approved maker-checker Approval referencing this version."""
    if not principal.can("rule.publish"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    v = await session.get(BillingRuleVersion, version_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not v.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    if v.status == "published":
        return {"ok": True, "status": v.status}
    await set_org_scope(session, v.org_path)
    if v.high_impact:
        approval = (
            await session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.entity_type == "billing_rule_version",
                    ApprovalRequest.entity_id == v.id,
                    ApprovalRequest.status == "approved",
                ).limit(1)
            )
        ).scalar_one_or_none()
        if approval is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail={
                "code": "approval_required",
                "message": "high-impact rule changes require an approved maker-checker request",
            })
    # supersede previous published version of the same rule
    prev = (
        await session.execute(
            select(BillingRuleVersion).where(
                BillingRuleVersion.rule_id == v.rule_id,
                BillingRuleVersion.status == "published",
            )
        )
    ).scalars().all()
    for p in prev:
        p.status = "retired"
    v.status = "published"
    v.approved_by = principal.user_id
    v.approved_at = datetime.now(UTC)
    rule = await session.get(BillingRule, v.rule_id)
    if rule:
        rule.status = "published"
    await record_audit(session, principal, action="billing_rule.published",
                       org_path=v.org_path,
                       summary=f"Rule '{rule.code if rule else '?'}' v{v.version_number} published",
                       entity_type="billing_rule_version", entity_id=v.id)
    await session.commit()
    return {"ok": True, "status": v.status}


@router.post("/billing-rule-versions/{version_id}/request-approval", status_code=201,
             dependencies=[CSRF])
async def request_high_impact_approval(version_id: uuid.UUID, session: SessionDep,
                                       principal: Principal):
    v = await session.get(BillingRuleVersion, version_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    await set_org_scope(session, v.org_path)
    from app.core.config import get_settings

    impact = v.estimated_impact_monthly if v.estimated_impact_monthly is not None else Decimal("0")
    threshold = Decimal(get_settings().approval_threshold_default)
    if not v.high_impact and impact < threshold:
        raise HTTPException(400, detail={"code": "not_high_impact"})
    n = (
        await session.execute(
            select(func.count(ApprovalRequest.id)).where(ApprovalRequest.kind == "rule_publish")
        )
    ).scalar_one()
    req = ApprovalRequest(
        request_number=f"APR-{int(n) + 1:05d}", kind="rule_publish",
        entity_type="billing_rule_version", entity_id=v.id,
        summary=f"Publish rule version {version_id} (impact ≈ {impact})",
        impact_amount=impact, maker_id=principal.user_id, org_path=v.org_path, org_id=v.org_id,
    )
    session.add(req)
    await record_audit(session, principal, action="approval.requested", org_path=v.org_path,
                       summary=f"Approval requested for rule version {version_id}",
                       entity_type="approval", entity_id=req.id)
    await session.commit()
    return {"id": str(req.id), "request_number": req.request_number}
