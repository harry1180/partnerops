"""Governance API (Phase 4): policies, findings, exceptions.

Policies are declarative rules evaluated against canonical cost rows (what
billing can observe); findings carry evidence and lifecycle (open →
acknowledged / remediated / excepted). Exceptions are time-boxed with reason
+ expiry; an expired exception reopens its finding on the next evaluation.
Provider-config facts (public buckets, unencrypted disks) are NOT faked —
policies whose kind needs them evaluate to zero findings until a config
connector exists (see services/governance.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.finops import (
    POLICY_KINDS,
    POLICY_SEVERITIES,
    GovernanceFinding,
    GovernancePolicy,
    PolicyException,
)
from app.services.audit_service import record_audit

router = APIRouter()


def _require(principal: Principal, perm: str) -> None:
    if not principal.can(perm):
        raise HTTPException(403, detail={"code": "forbidden"})


def _partner_root(principal: Principal) -> str:
    if principal.is_platform_admin or principal.org_kind == "customer":
        raise HTTPException(400, detail={"code": "platform_admin_must_scope"})
    return principal.org_path


class PolicyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    kind: str
    severity: str = "medium"
    parameters: dict = Field(default_factory=dict)
    owner_label: str | None = Field(default=None, max_length=255)
    remediation: str | None = Field(default=None, max_length=2000)


@router.post("/governance/policies", status_code=201, dependencies=[CSRF])
async def create_policy(body: PolicyCreate, session: SessionDep, principal: Principal):
    _require(principal, "policy.manage")
    if body.kind not in POLICY_KINDS:
        raise HTTPException(422, detail={"code": "bad_kind", "allowed": list(POLICY_KINDS)})
    if body.severity not in POLICY_SEVERITIES:
        raise HTTPException(422, detail={"code": "bad_severity"})
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    pol = GovernancePolicy(
        org_id=principal.org_id, org_path=org_path, name=body.name, kind=body.kind,
        severity=body.severity, parameters=body.parameters, owner_label=body.owner_label,
        remediation=body.remediation, created_by=principal.user_id,
    )
    session.add(pol)
    await session.flush()
    await record_audit(session, principal, action="governance.policy_created", org_path=org_path,
                       summary=f"Policy '{body.name}' ({body.kind}, {body.severity})",
                       entity_type="governance_policy", entity_id=pol.id)
    await session.commit()
    return {"id": str(pol.id), "kind": pol.kind}


@router.get("/governance/policies")
async def list_policies(session: SessionDep, principal: Principal):
    _require(principal, "customer.read")
    root = principal.scope_prefixes[0]
    rows = (
        await session.execute(
            select(GovernancePolicy).where(
                GovernancePolicy.org_path.like(root + "%"),
                GovernancePolicy.deleted_at.is_(None),
            ).order_by(GovernancePolicy.kind)
        )
    ).scalars().all()
    counts = {pid: int(n) for pid, n in (
        await session.execute(
            select(GovernanceFinding.policy_id, func.count(GovernanceFinding.id))
            .where(GovernanceFinding.org_path.like(root + "%"),
                   GovernanceFinding.status == "open")
            .group_by("policy_id")
        )
    ).all()}
    return {"items": [{
        "id": str(p.id), "name": p.name, "kind": p.kind, "severity": p.severity,
        "enabled": p.enabled, "parameters": p.parameters, "owner_label": p.owner_label,
        "remediation": p.remediation,
        "open_findings": int(counts.get(p.id, 0)),
        "last_evaluated_at": p.last_evaluated_at.isoformat() if p.last_evaluated_at else None,
    } for p in rows]}


class PolicyToggle(BaseModel):
    enabled: bool


@router.post("/governance/policies/{policy_id}/toggle", dependencies=[CSRF])
async def toggle_policy(policy_id: uuid.UUID, body: PolicyToggle,
                        session: SessionDep, principal: Principal):
    _require(principal, "policy.manage")
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    pol = await session.get(GovernancePolicy, policy_id)
    if pol is None or pol.deleted_at is not None or not pol.org_path.startswith(org_path):
        raise HTTPException(404, detail={"code": "not_found"})
    pol.enabled = body.enabled
    await record_audit(session, principal, action="governance.policy_updated",
                       org_path=pol.org_path,
                       summary=f"Policy '{pol.name}' {'enabled' if body.enabled else 'disabled'}",
                       entity_type="governance_policy", entity_id=pol.id)
    await session.commit()
    return {"ok": True, "enabled": pol.enabled}


@router.post("/governance/evaluate", dependencies=[CSRF])
async def evaluate(session: SessionDep, principal: Principal):
    _require(principal, "policy.manage")
    from app.services import governance as gsvc

    org_path = _partner_root(principal)
    res = await gsvc.evaluate_policies(session, org_path)
    await record_audit(session, principal, action="governance.evaluated", org_path=org_path,
                       summary=(f"Governance evaluation: {res.findings_new} new, "
                                f"{res.findings_remediated} remediated, "
                                f"{res.findings_excepted} excepted, "
                                f"{res.exceptions_expired} exceptions expired"),
                       entity_type="governance_policy", entity_id=None,
                       detail={"by_policy": res.by_policy})
    await session.commit()
    return {"new": res.findings_new, "open": res.findings_open,
            "remediated": res.findings_remediated, "excepted": res.findings_excepted,
            "exceptions_expired": res.exceptions_expired, "by_policy": res.by_policy}


@router.get("/governance/findings")
async def list_findings(session: SessionDep, principal: Principal,
                        status_: str | None = Query(None, alias="status"),
                        page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    _require(principal, "customer.read")
    root = principal.scope_prefixes[0]
    stmt = select(GovernanceFinding).where(GovernanceFinding.org_path.like(root + "%"))
    if status_:
        stmt = stmt.where(GovernanceFinding.status == status_)
    total = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(GovernanceFinding.last_seen.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    pol_ids = {r.policy_id for r in rows}
    pols = {}
    if pol_ids:
        pols = {p.id: p.name for p in (await session.execute(
            select(GovernancePolicy).where(GovernancePolicy.id.in_(pol_ids)))).scalars()}
    active_exc = {
        e.finding_id for e in (await session.execute(
            select(PolicyException).where(
                PolicyException.org_path.like(root + "%"),
                PolicyException.revoked_at.is_(None),
                PolicyException.expires_at > datetime.now(UTC))
        )).scalars()
    }
    return {"items": [{
        "id": str(f.id), "policy_id": str(f.policy_id),
        "policy_name": pols.get(f.policy_id, ""),
        "subject": f.subject, "severity": f.severity, "status": f.status,
        "customer_id": str(f.customer_id) if f.customer_id else None,
        "evidence": f.evidence, "remediation": f.remediation,
        "owner_label": f.owner_label, "first_seen": f.first_seen.isoformat(),
        "last_seen": f.last_seen.isoformat(), "has_active_exception": f.id in active_exc,
    } for f in rows], "total": total, "page": page, "page_size": page_size}


class FindingAck(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/governance/findings/{finding_id}/acknowledge", dependencies=[CSRF])
async def acknowledge_finding(finding_id: uuid.UUID, body: FindingAck,
                              session: SessionDep, principal: Principal):
    _require(principal, "finding.review")
    root = principal.scope_prefixes[0]
    f = await session.get(GovernanceFinding, finding_id)
    if f is None or not f.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    if f.status not in ("open", "acknowledged"):
        raise HTTPException(409, detail={"code": "not_ackable", "status": f.status})
    f.status = "acknowledged"
    f.acknowledged_by = principal.user_id
    f.notes = ((f.notes or "") + f"\n[{principal.email}] {body.note or ''}").strip()
    await record_audit(session, principal, action="governance.finding_acknowledged",
                       org_path=f.org_path, summary=f"Finding {f.subject} acknowledged",
                       entity_type="governance_finding", entity_id=f.id)
    await session.commit()
    return {"ok": True, "status": f.status}


class ExceptionCreate(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    expires_at: datetime


@router.post("/governance/findings/{finding_id}/exception", status_code=201,
             dependencies=[CSRF])
async def grant_exception(finding_id: uuid.UUID, body: ExceptionCreate,
                          session: SessionDep, principal: Principal):
    _require(principal, "policy.manage")
    if body.expires_at <= datetime.now(UTC):
        raise HTTPException(422, detail={"code": "expiry_in_past"})
    root = principal.scope_prefixes[0]
    f = await session.get(GovernanceFinding, finding_id)
    if f is None or not f.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    await set_org_scope(session, f.org_path)
    existing = (
        await session.execute(
            select(PolicyException).where(PolicyException.org_path == f.org_path,
                                          PolicyException.finding_id == f.id)
        )
    ).scalar_one_or_none()
    exc = existing or PolicyException(
        org_id=principal.org_id, org_path=f.org_path, finding_id=f.id)
    exc.reason, exc.expires_at, exc.approved_by = body.reason, body.expires_at, principal.user_id
    exc.revoked_at = None
    session.add(exc)
    f.status = "excepted"
    await record_audit(session, principal, action="governance.exception_granted",
                       org_path=f.org_path,
                       summary=f"Exception granted for finding {f.subject} until "
                               f"{body.expires_at:%Y-%m-%d}: {body.reason[:120]}",
                       entity_type="policy_exception", entity_id=exc.id)
    await session.commit()
    return {"id": str(exc.id), "expires_at": exc.expires_at.isoformat()}
