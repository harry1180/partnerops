"""Maker-checker approvals: the generic approval queue + decision logic.

Rules (docs/decisions, charter):
- The maker (requester) may never decide their own request.
- Deciders need the permission matching the request kind.
- Decisions are immutable once made (approved/denied/cancelled are terminal
  except cancel-by-maker while pending).
- Every request/decision is audited.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.approvals import ApprovalRequest
from app.models.auth import User
from app.services.audit_service import record_audit

router = APIRouter()

# kind → permission required to decide it
KIND_DECIDE_PERMISSION: dict[str, str] = {
    "rule_publish": "rule.approve",
    "contract_overlap": "contract.approve",
    "contract_activate": "contract.approve",
    "manual_adjustment": "invoice.approve",
    "recon_waiver": "recon.waive",
    "period_close_waiver": "period.close",
    "invoice_issue": "invoice.issue",
}


class DecisionBody(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


@router.get("/approvals")
async def list_approvals(session: SessionDep, principal: Principal,
                         status_filter: str | None = Query(None, alias="status",
                                                           pattern="^(pending|approved|denied|cancelled)$"),
                         page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    deciders = ("rule.approve", "contract.approve", "recon.waive",
                "invoice.approve", "invoice.correct", "period.close")
    if not any(principal.can(p_) for p_ in deciders) and not principal.can("audit.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    conds: list = [ApprovalRequest.org_path.like(root + "%")]
    if status_filter:
        conds.append(ApprovalRequest.status == status_filter)
    total = int((await session.execute(
        select(func.count(ApprovalRequest.id)).where(*conds))).scalar_one())
    rows = (await session.execute(
        select(ApprovalRequest, User.display_name, User.email)
        .join(User, User.id == ApprovalRequest.maker_id)
        .where(*conds).order_by(ApprovalRequest.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [{
        "id": str(r.id), "request_number": r.request_number, "kind": r.kind,
        "entity_type": r.entity_type, "entity_id": str(r.entity_id),
        "summary": r.summary,
        "impact_amount": str(r.impact_amount) if r.impact_amount is not None else None,
        "currency": r.currency, "status": r.status,
        "maker": maker_name, "maker_email": maker_email,
        "created_at": r.created_at.isoformat(),
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decision_note": r.decision_note,
    } for r, maker_name, maker_email in rows],
        "total": total, "page": page, "page_size": page_size}


@router.post("/approvals/{approval_id}/decide", dependencies=[CSRF])
async def decide(approval_id: uuid.UUID, body: DecisionBody, session: SessionDep,
                 principal: Principal, decision: str = Query(..., pattern="^(approved|denied|cancelled)$")):
    req = await session.get(ApprovalRequest, approval_id)
    if req is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not req.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    if req.status != "pending":
        raise HTTPException(409, detail={"code": "already_decided", "status": req.status})
    # cancel is the maker's right while pending; approve/deny needs the gate
    if decision == "cancelled":
        if req.maker_id != principal.user_id:
            raise HTTPException(403, detail={"code": "only_maker_can_cancel"})
    else:
        perm = KIND_DECIDE_PERMISSION.get(req.kind)
        if perm is None or not principal.can(perm):
            raise HTTPException(403, detail={"code": "forbidden", "required": perm or "n/a"})
        if req.maker_id == principal.user_id:
            raise HTTPException(403, detail={
                "code": "self_approval",
                "message": "maker-checker: you requested this approval and cannot decide it",
            })
    await set_org_scope(session, req.org_path)
    req.status = decision
    req.decision_note = body.note
    req.decided_at = datetime.now(UTC)
    req.checker_id = principal.user_id
    await record_audit(session, principal,
                       action={"approved": "approval.granted", "denied": "approval.denied",
                               "cancelled": "approval.cancelled"}[decision],
                       org_path=req.org_path,
                       summary=f"{req.request_number} ({req.kind}) {decision}",
                       entity_type="approval", entity_id=req.id,
                       detail={"note": body.note[:500]})
    await session.commit()
    return {"ok": True, "status": req.status}
