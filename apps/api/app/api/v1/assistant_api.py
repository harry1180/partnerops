"""Assistant API (Phase 5).

POST /assistant/ask — deterministic retrieval answers, permission-enforced,
fully audited (AIQueryAudit + audit event for sensitive queries).
GET  /assistant/capabilities — what the assistant can and cannot do, in
plain language, so the UI can be honest about demo mode.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.assistant import AIQueryAudit
from app.models.auth import User
from app.services.assistant import ask as run_ask

router = APIRouter()

CAPABILITIES = {
    "mode": "deterministic_demo",
    "model_backed": False,
    "read_only": True,
    "can_write": False,
    "intents": [
        {"id": "invoice_change", "example": "Why did Acme's invoice increase this month?",
         "requires": ["invoice.read"]},
        {"id": "rules_on_invoice", "example": "Which billing rules affected INV-…?",
         "requires": ["invoice.read"]},
        {"id": "lineage", "example": "Show the source records behind invoice line 3.",
         "requires": ["invoice.read"]},
        {"id": "margin_below", "example": "Which customers are below target margin?",
         "requires": ["margin.view"], "partner_only": True},
        {"id": "unbilled", "example": "Are there unbilled accounts this period?",
         "requires": ["cost.read"]},
        {"id": "unallocated_credits", "example": "Which credits have not been allocated?",
         "requires": ["customer.read"], "partner_only": True},
        {"id": "recon_why", "example": "What caused the reconciliation difference?",
         "requires": ["recon.read"], "partner_only": True},
        {"id": "top_savings", "example": "Which recommendations have the highest verified savings?",
         "requires": ["cost.read"], "partner_only": True},
        {"id": "summarize_anomalies", "example": "Summarize this customer's cost anomalies.",
         "requires": ["cost.read"]},
        {"id": "draft_customer_safe", "example": "Draft a customer-safe explanation of this invoice change.",
         "requires": ["invoice.read"]},
    ],
    "guarantees": [
        "Answers are computed live from records your role may read; nothing is generated.",
        "Every answer cites record ids; every refusal explains itself.",
        "Facts and (labeled) estimates are separated; no invented charges or savings.",
        "Margin and provider-cost intents are refused for customer roles.",
        "Every query is written to the AI audit trail, including refusals.",
    ],
}


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@router.get("/assistant/capabilities")
async def capabilities(principal: Principal):
    if not principal.can("assistant.ask"):
        raise HTTPException(403, detail={"code": "forbidden"})
    return CAPABILITIES


@router.post("/assistant/ask", dependencies=[CSRF])
async def ask(body: AskIn, session: SessionDep, principal: Principal):
    if not principal.can("assistant.ask"):
        raise HTTPException(403, detail={"code": "forbidden"})
    answer = await run_ask(session, principal, body.question)
    if answer.sensitive and not answer.refused:
        # second, human-searchable audit event beyond the AIQueryAudit row
        from app.services.audit_service import record_audit
        await set_org_scope(session, principal.org_path)
        await record_audit(
            session, principal, action="assistant.query_sensitive",
            org_path=principal.org_path,
            summary=f"Sensitive assistant query: {answer.intent} ({body.question[:80]})",
            entity_type="ai_query", entity_id=None,
            detail={"intent": answer.intent, "tools": answer.tools_used})
        await session.commit()
    return {
        "intent": answer.intent, "mode": answer.mode, "text": answer.text,
        "facts": answer.facts, "estimates": answer.estimates,
        "citations": answer.citations, "refused": answer.refused,
        "refusal_reason": answer.refusal_reason, "sensitive": answer.sensitive,
        "tools_used": answer.tools_used, "latency_ms": answer.latency_ms,
    }


@router.get("/assistant/audit")
async def assistant_audit(session: SessionDep, principal: Principal,
                          page: int = 1, page_size: int = 25):
    """Auditors/admins: the AI query trail within scope."""
    if not principal.can("audit.read"):
        raise HTTPException(403, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    stmt = select(AIQueryAudit, User.email).join(User, User.id == AIQueryAudit.asked_by).where(
        AIQueryAudit.org_path.like(root + "%"))
    total = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(AIQueryAudit.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [{
        "id": str(a.id), "asked_by_email": email,
        "question": a.question[:200],
        "intent": a.intent, "mode": a.mode, "refused": a.refused,
        "refusal_reason": a.refusal_reason, "sensitive": a.sensitive,
        "tools_used": a.tools_used, "citations": a.citations,
        "answer_summary": a.answer_summary, "latency_ms": a.latency_ms,
        "created_at": a.created_at.isoformat(),
    } for a, email in rows], "total": total}
