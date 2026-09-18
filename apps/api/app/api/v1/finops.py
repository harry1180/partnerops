"""FinOps API (Phase 4): budgets, anomalies, recommendations, forecast.

Permission model:
- budget.manage: create/update budgets (partner roles + customer_admin on
  their own budgets)
- anomaly.review / optimization.review: triage workflows (finops_analyst,
  partner admins) — read is cost.read
- detection passes are manual here (and scheduled in workers) and audited.

Nothing here claims to see provider resources: every number derives from
canonical cost rows and records its method.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.billing_core import Customer
from app.models.finops import (
    ANOMALY_STATUSES,
    Budget,
    CostAnomaly,
    Recommendation,
)
from app.services import alerts
from app.services import budgets as bsvc
from app.services.audit_service import record_audit

router = APIRouter()


def _require(principal: Principal, perm: str) -> None:
    if not principal.can(perm):
        raise HTTPException(403, detail={"code": "forbidden"})


def _partner_root(principal: Principal) -> str:
    if principal.is_platform_admin or principal.org_kind == "customer":
        raise HTTPException(400, detail={"code": "platform_admin_must_scope"})
    return principal.org_path


class BudgetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    amount: str = Field(pattern=r"^\d+(\.\d{1,6})?$")
    period_start: datetime
    period_end: datetime
    customer_id: uuid.UUID | None = None
    provider_code: str | None = Field(default=None, max_length=32)
    alert_threshold_pct: int = Field(default=80, ge=10, le=200)


@router.post("/budgets", status_code=201, dependencies=[CSRF])
async def create_budget(body: BudgetCreate, session: SessionDep, principal: Principal):
    _require(principal, "budget.manage")
    org_path = _partner_root(principal)
    if body.period_end <= body.period_start:
        raise HTTPException(422, detail={"code": "bad_period"})
    scope_kind = "org"
    row_path = org_path
    if body.customer_id:
        cust = await session.get(Customer, body.customer_id)
        if cust is None or not cust.org_path.startswith(org_path):
            raise HTTPException(404, detail={"code": "not_found"})
        scope_kind = "customer"
        # store under the customer's own subtree so the portal (RLS-bound to
        # the customer path) can read it; still inside the partner scope.
        row_path = cust.org_path
    await set_org_scope(session, org_path)
    b = Budget(
        org_id=principal.org_id, org_path=row_path, name=body.name,
        scope_kind=scope_kind, customer_id=body.customer_id,
        provider_code=body.provider_code, amount=Decimal(body.amount),
        period_start=body.period_start, period_end=body.period_end,
        alert_threshold_pct=body.alert_threshold_pct, created_by=principal.user_id,
    )
    session.add(b)
    await session.flush()
    await record_audit(session, principal, action="budget.created", org_path=org_path,
                       summary=f"Budget '{body.name}' {b.amount} {b.currency} "
                               f"{body.period_start:%Y-%m}..{body.period_end:%Y-%m}",
                       entity_type="budget", entity_id=b.id)
    await session.commit()
    return {"id": str(b.id), "name": b.name, "amount": str(b.amount)}


@router.get("/budgets")
async def list_budgets(session: SessionDep, principal: Principal,
                       include_closed: bool = False):
    _require(principal, "cost.read")
    org_path = _partner_root(principal)
    statuses = await bsvc.list_budget_statuses(session, org_path, active_only=not include_closed)
    return {"items": [{
        "id": str(s.budget.id), "name": s.budget.name,
        "scope_kind": s.budget.scope_kind,
        "customer_id": str(s.budget.customer_id) if s.budget.customer_id else None,
        "provider": s.budget.provider_code, "currency": s.budget.currency,
        "amount": str(s.budget.amount),
        "period_start": s.budget.period_start.isoformat(),
        "period_end": s.budget.period_end.isoformat(),
        "actual": str(s.actual), "projected_total": str(s.projected_total),
        "pct_of_budget": str(s.pct_of_budget), "projected_pct": str(s.projected_pct),
        "alert_threshold_pct": s.budget.alert_threshold_pct,
        "over_threshold": s.over_threshold, "over_budget": s.over_budget,
        "alert_state": s.budget.alert_state,
        "days_elapsed": s.days_elapsed, "days_total": s.days_total,
    } for s in statuses]}


@router.delete("/budgets/{budget_id}", dependencies=[CSRF])
async def delete_budget(budget_id: uuid.UUID, session: SessionDep, principal: Principal):
    _require(principal, "budget.manage")
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    b = await session.get(Budget, budget_id)
    if b is None or b.deleted_at is not None or not b.org_path.startswith(org_path):
        raise HTTPException(404, detail={"code": "not_found"})
    b.deleted_at = datetime.now(UTC)
    await record_audit(session, principal, action="budget.deleted", org_path=b.org_path,
                       summary=f"Budget '{b.name}' deleted (soft)",
                       entity_type="budget", entity_id=b.id)
    await session.commit()
    return {"ok": True}


@router.post("/budgets/evaluate-alerts", dependencies=[CSRF])
async def evaluate_alerts(session: SessionDep, principal: Principal):
    """Manual alert pass (same episode logic as the nightly): opened
    episodes queue the budget.over_threshold webhook + emails."""
    _require(principal, "alert.manage")
    org_path = _partner_root(principal)
    res = await alerts.evaluate_budget_alerts(session, org_path)
    await record_audit(session, principal, action="budget.alerts_evaluated",
                       org_path=org_path,
                       summary=(f"Budget alert pass: {res.evaluated} evaluated, "
                                f"{res.opened} opened, {res.closed} recovered, "
                                f"{res.still_open} still open"),
                       entity_type="budget", entity_id=None,
                       detail=res.as_dict())
    await session.commit()
    return res.as_dict()


@router.post("/finops/anomalies/run", dependencies=[CSRF])
async def run_anomalies(session: SessionDep, principal: Principal,
                        threshold_z: str = "4.0", abs_floor: str = "500",
                        rel_floor_pct: str = "50"):
    _require(principal, "anomaly.review")
    from app.services import anomaly

    org_path = _partner_root(principal)
    res = await anomaly.run_anomaly_pass(
        session, org_path, threshold_z=Decimal(threshold_z),
        abs_floor=Decimal(abs_floor), rel_floor_pct=Decimal(rel_floor_pct))
    await record_audit(session, principal, action="anomaly.pass_ran", org_path=org_path,
                       summary=f"Anomaly pass: {res.created} new, {res.updated} updated "
                               f"(z>={threshold_z}, floor {abs_floor})",
                       entity_type="cost_anomaly", entity_id=None,
                       detail={"created": res.created, "updated": res.updated})
    await session.commit()
    return {"created": res.created, "updated": res.updated, "subjects": res.subjects}


@router.get("/finops/anomalies")
async def list_anomalies(session: SessionDep, principal: Principal,
                         status_: str | None = Query(None, alias="status"),
                         page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    # partner FinOps surface: customer roles never see anomaly analysis
    # (their portal budget/anomaly view is a scoped projection, Phase 5)
    _require(principal, "anomaly.review")
    root = principal.scope_prefixes[0]
    stmt = select(CostAnomaly).where(CostAnomaly.org_path.like(root + "%"))
    if status_:
        stmt = stmt.where(CostAnomaly.status == status_)
    total = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(CostAnomaly.detected_on.desc(), CostAnomaly.observed_amount.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [{
        "id": str(a.id), "kind": a.kind, "status": a.status,
        "customer_id": str(a.customer_id) if a.customer_id else None,
        "service": a.service, "detected_on": a.detected_on.isoformat(),
        "observed": str(a.observed_amount), "baseline": str(a.baseline_amount),
        "z_score": str(a.z_score), "threshold_used": str(a.threshold_used),
        "method": a.method, "evidence": a.evidence, "review_note": a.review_note,
    } for a in rows], "total": total, "page": page, "page_size": page_size}


class AnomalyReview(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)


@router.patch("/finops/anomalies/{anomaly_id}", dependencies=[CSRF])
async def review_anomaly(anomaly_id: uuid.UUID, body: AnomalyReview,
                         session: SessionDep, principal: Principal):
    _require(principal, "anomaly.review")
    if body.status not in ANOMALY_STATUSES:
        raise HTTPException(422, detail={"code": "bad_status"})
    root = principal.scope_prefixes[0]
    a = await session.get(CostAnomaly, anomaly_id)
    if a is None or not a.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    a.status = body.status
    a.review_note = body.note
    a.reviewed_by = principal.user_id
    a.reviewed_at = datetime.now(UTC)
    await record_audit(session, principal, action="anomaly.reviewed", org_path=a.org_path,
                       summary=f"Anomaly {a.kind} ({a.service or a.customer_id}) → {body.status}",
                       entity_type="cost_anomaly", entity_id=a.id)
    await session.commit()
    return {"ok": True, "status": a.status}


@router.post("/finops/recommendations/run", dependencies=[CSRF])
async def run_recommendations(session: SessionDep, principal: Principal):
    _require(principal, "optimization.review")
    from app.services import recommendations as rsvc

    org_path = _partner_root(principal)
    res = await rsvc.run_recommendation_pass(session, org_path)
    realized = await rsvc.realize_savings(session, org_path)
    await record_audit(session, principal, action="recommendation.pass_ran", org_path=org_path,
                       summary=f"Recommendation pass: {res.created} new, {res.refreshed} refreshed; "
                               f"{realized} savings realized",
                       entity_type="recommendation", entity_id=None,
                       detail={"created": res.created, "refreshed": res.refreshed,
                               "realized": realized})
    await session.commit()
    return {"created": res.created, "refreshed": res.refreshed,
            "resources": res.resources_evaluated, "realized": realized}


@router.get("/finops/recommendations")
async def list_recommendations(session: SessionDep, principal: Principal,
                               status_: str | None = Query(None, alias="status"),
                               kind: str | None = None,
                               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    _require(principal, "cost.read")
    root = principal.scope_prefixes[0]
    stmt = select(Recommendation).where(Recommendation.org_path.like(root + "%"))
    if status_:
        stmt = stmt.where(Recommendation.status == status_)
    if kind:
        stmt = stmt.where(Recommendation.kind == kind)
    total = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(Recommendation.estimated_monthly_saving.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    est_sum = sum((Decimal(r.estimated_monthly_saving or 0) for r in rows
                   if r.status == "open"), Decimal("0"))
    real_sum = sum((Decimal(r.realized_savings or 0) for r in rows
                    if r.realized_savings), Decimal("0"))
    return {"items": [{
        "id": str(r.id), "kind": r.kind, "status": r.status,
        "customer_id": str(r.customer_id) if r.customer_id else None,
        "resource_id": r.resource_id, "service": r.service, "region": r.region,
        "title": r.title, "detail": r.detail, "remediation": r.remediation,
        "estimated_monthly_saving": str(r.estimated_monthly_saving)
        if r.estimated_monthly_saving is not None else None,
        "realized_savings": str(r.realized_savings) if r.realized_savings is not None else None,
        "realized_basis": r.realized_basis,
        "confidence": r.confidence, "basis": r.basis,
        "decision_note": r.decision_note,
    } for r in rows], "total": total, "page": page, "page_size": page_size,
        "open_estimated_total": str(est_sum), "realized_total": str(real_sum)}


class Decision(BaseModel):
    decision: str  # accept | dismiss
    note: str | None = Field(default=None, max_length=2000)


@router.post("/finops/recommendations/{rec_id}/decision", dependencies=[CSRF])
async def decide_recommendation(rec_id: uuid.UUID, body: Decision,
                                session: SessionDep, principal: Principal):
    _require(principal, "optimization.review")
    if body.decision not in ("accept", "dismiss"):
        raise HTTPException(422, detail={"code": "bad_decision"})
    root = principal.scope_prefixes[0]
    r = await session.get(Recommendation, rec_id)
    if r is None or not r.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    r.status = "accepted" if body.decision == "accept" else "dismissed"
    r.decided_by = principal.user_id
    r.decided_at = datetime.now(UTC)
    r.decision_note = body.note
    await record_audit(session, principal, action="recommendation.decided", org_path=r.org_path,
                       summary=f"Recommendation '{r.title}' {body.decision}ed",
                       entity_type="recommendation", entity_id=r.id)
    await session.commit()
    return {"ok": True, "status": r.status}


@router.get("/finops/forecast")
async def forecast(session: SessionDep, principal: Principal,
                   months_ahead: int = Query(3, ge=1, le=12)):
    _require(principal, "cost.read")
    org_path = _partner_root(principal)
    return await bsvc.org_forecast(session, org_path, months_ahead)


@router.get("/finops/unit-economics")
async def unit_economics(session: SessionDep, principal: Principal,
                         period_start: datetime | None = None):
    """Cost per application/environment/owner dimension — allocation rollups
    the charter's unit-economics requirement serves from canonical rows."""
    _require(principal, "cost.read")
    root = principal.scope_prefixes[0]
    dims = []
    from app.models.cost import CanonicalCostRecord as C

    for dim in ("application", "environment", "owner", "cost_center"):
        col = getattr(C, dim)
        stmt = (
            select(col.label("k"),
                   func.sum(C.provider_billed).label("amt"),
                   func.count(C.id).label("n"))
            .where(C.org_path.like(root + "%"),
                   C.line_item_type == "usage",
                   col.isnot(None))
            .group_by("k").order_by(func.sum(C.provider_billed).desc())
            .limit(20))
        if period_start:
            stmt = stmt.where(C.billing_period_start == period_start)
        dims.append({
            "dimension": dim,
            "items": [{"key": str(k), "amount": str(a or 0), "rows": int(n)}
                      for k, a, n in (await session.execute(stmt)).all()],
        })
    return {"dimensions": dims}


@router.get("/finops/tag-compliance")
async def tag_compliance(session: SessionDep, principal: Principal):
    _require(principal, "cost.read")
    from app.services.tagging import REQUIRED_TAG_KEYS, TagCoverage

    root = principal.scope_prefixes[0]
    out = []
    from app.models.cost import CanonicalCostRecord as C

    for key in REQUIRED_TAG_KEYS:
        col = getattr(C, key, None)
        if col is None:
            continue
        total, have = (
            await session.execute(
                select(func.count(C.id),
                       func.count(col).filter(col != ""))
                .where(C.org_path.like(root + "%"),
                       C.customer_id.isnot(None),
                       C.line_item_type == "usage")
            )
        ).one()
        cov = TagCoverage(compliant=int(have or 0), total=int(total or 0))
        out.append({"key": key, "compliant": cov.compliant, "total": cov.total,
                    "pct": cov.pct})
    unallocated = (
        await session.execute(
            select(func.coalesce(func.sum(C.provider_billed), 0))
            .where(C.org_path.like(root + "%"), C.customer_id.is_(None),
                   C.line_item_type.in_(("usage", "support")))
        )
    ).scalar_one()
    return {"coverage": out,
            "unallocated_cost": str(Decimal(unallocated or 0).quantize(Decimal("0.01")))}
