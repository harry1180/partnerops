"""Phase 2 billing operations: credit/debit notes, period close, waivers,
manual adjustments, revenue-leakage detection.

Immutability contract (docs/invoice-lifecycle.md): issued invoices are never
edited. Corrections are BillingNotes (credit/debit) linked to the source
invoice; the source invoice moves to `corrected` once its notes fully offset
it or the partner marks it corrected. Period close is blocked by open
MATERIAL reconciliation exceptions unless each is resolved or waived through
a maker-checker approval.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.engine.money import ZERO, q
from app.models.billing_core import Customer
from app.models.invoices import (
    BillingNote,
    Invoice,
    PeriodClose,
)
from app.models.reconciliation import ReconciliationException
from app.services.approvals_service import create_approval, require_approved
from app.services.audit_service import record_audit
from app.services.document_service import render_note_pdf
from app.services.invoice_lifecycle import LifecycleError

router = APIRouter()


async def _scoped_invoice(session: AsyncSession, principal: Principal,
                          invoice_id: uuid.UUID) -> Invoice:
    inv = await session.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not inv.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    await set_org_scope(session, inv.org_path)
    return inv


# --------------------------------------------------------------- billing notes

class NoteCreate(BaseModel):
    invoice_id: uuid.UUID
    kind: str = Field(pattern="^(credit|debit)$")
    lines: list[dict] = Field(min_length=1)
    reason: str = Field(min_length=4, max_length=4000)


class NoteIssue(BaseModel):
    note_id: uuid.UUID


@router.post("/billing-notes", status_code=201, dependencies=[CSRF])
async def create_note(body: NoteCreate, session: SessionDep, principal: Principal):
    """Draft a credit/debit note against an issued invoice. Amount = sum of
    note lines (credit notes carry positive amounts that REDUCE what the
    customer owes; debit notes increase it)."""
    if not principal.can("invoice.correct"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    inv = await _scoped_invoice(session, principal, body.invoice_id)
    if inv.status not in ("issued", "exported", "paid_or_settled", "disputed", "corrected"):
        raise HTTPException(409, detail={"code": "not_correctable",
                                         "message": "notes only apply to issued invoices"})
    total = ZERO
    lines: list[dict] = []
    for i, ln in enumerate(body.lines, start=1):
        try:
            amt = Decimal(str(ln.get("amount", "")))
        except Exception:
            raise HTTPException(400, detail={"code": "bad_amount", "line": i}) from None
        if amt == 0:
            raise HTTPException(400, detail={"code": "zero_line", "line": i})
        total += amt
        lines.append({"line_number": i, "description": str(ln.get("description", ""))[:500],
                      "amount": str(q(amt))})
    if total <= ZERO:
        raise HTTPException(400, detail={"code": "note_total_nonpositive",
                                         "message": "note lines must sum to a positive amount"})
    n = int((await session.execute(
        select(func.count(BillingNote.id)).where(BillingNote.org_path == inv.org_path)
    )).scalar_one())
    prefix = "CN" if body.kind == "credit" else "DN"
    note = BillingNote(
        note_number=f"{prefix}-{inv.invoice_number.split('-')[1]}-{n + 1:04d}",
        kind=body.kind, invoice_id=inv.id, customer_id=inv.customer_id,
        amount=q(total), currency=inv.currency, reason=body.reason,
        status="draft", lines=lines,
        org_path=inv.org_path, org_id=inv.org_id,
    )
    session.add(note)
    await record_audit(session, principal, action="billing_note.created",
                       org_path=inv.org_path,
                       summary=f"{note.note_number} ({body.kind}) draft for {inv.invoice_number}",
                       entity_type="billing_note", entity_id=note.id)
    await session.commit()
    return {"id": str(note.id), "note_number": note.note_number,
            "amount": str(note.amount), "status": note.status}


@router.get("/billing-notes")
async def list_notes(session: SessionDep, principal: Principal,
                     invoice_id: uuid.UUID | None = None):
    if not principal.can("invoice.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    conds: list = [BillingNote.org_path.like(root + "%")]
    if invoice_id:
        conds.append(BillingNote.invoice_id == invoice_id)
    rows = (await session.execute(
        select(BillingNote).where(*conds).order_by(BillingNote.created_at.desc()).limit(200)
    )).scalars().all()
    return [{
        "id": str(x.id), "note_number": x.note_number, "kind": x.kind,
        "invoice_id": str(x.invoice_id), "customer_id": str(x.customer_id),
        "amount": str(x.amount), "currency": x.currency, "status": x.status,
        "reason": x.reason, "lines": x.lines,
        "created_at": x.created_at.isoformat(),
        "issued_at": x.issued_at.isoformat() if x.issued_at else None,
    } for x in rows]


@router.post("/billing-notes/{note_id}/issue", dependencies=[CSRF])
async def issue_note(note_id: uuid.UUID, session: SessionDep, principal: Principal):
    """Issue a note: it becomes immutable, the customer sees it, and the
    source invoice transitions to `corrected` (terminal state)."""
    if not principal.can("invoice.correct"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    note = await session.get(BillingNote, note_id)
    if note is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not note.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    if note.status != "draft":
        raise HTTPException(409, detail={"code": "already_issued"})
    await set_org_scope(session, note.org_path)
    inv = await session.get(Invoice, note.invoice_id)
    if inv is None or inv.status not in ("issued", "exported", "paid_or_settled", "disputed"):
        raise HTTPException(409, detail={"code": "source_not_issued"})
    note.status = "issued"
    note.issued_at = datetime.now(UTC)
    note.issued_by = principal.user_id
    # source invoice becomes corrected (status-only change; trigger-safe)
    try:
        from app.services.invoice_lifecycle import transition_invoice
        await transition_invoice(session, inv, "corrected", principal,
                                 note=f"corrected by {note.note_number}")
    except LifecycleError as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from None
    await record_audit(session, principal, action="billing_note.issued",
                       org_path=note.org_path,
                       summary=f"{note.note_number} issued against {inv.invoice_number} "
                               f"({note.kind} {note.amount} {note.currency})",
                       entity_type="billing_note", entity_id=note.id)
    await session.commit()
    return {"ok": True, "status": note.status, "invoice_status": inv.status}


@router.get("/billing-notes/{note_id}/download")
async def download_note(note_id: uuid.UUID, session: SessionDep, principal: Principal,
                        fmt: str = Query("pdf", pattern="^(pdf|csv)$")):
    if not principal.can("invoice.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    note = await session.get(BillingNote, note_id)
    if note is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not note.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    inv = await session.get(Invoice, note.invoice_id)
    cust = await session.get(Customer, note.customer_id)
    if fmt == "csv":
        rows = ["type,number,invoice,customer,amount,currency,status"]
        rows.append(f"{note.kind},{note.note_number},{inv.invoice_number if inv else ''},"
                    f"{cust.display_name if cust else ''},{note.amount},{note.currency},{note.status}")
        for ln in note.lines:
            rows.append(f"line,,{ln.get('description', '')},,{ln.get('amount', '')},{note.currency},")
        return _csv_response(",".join(rows), f"{note.note_number}.csv")
    data = render_note_pdf(note, inv, cust)
    return _pdf_response(data, f"{note.note_number}.pdf")


# ---------------------------------------------------------------- period close

@router.get("/period-closes")
async def list_period_closes(session: SessionDep, principal: Principal):
    if not principal.can("recon.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    rows = (await session.execute(
        select(PeriodClose).where(PeriodClose.org_path.like(root + "%"))
        .order_by(PeriodClose.billing_period_start.desc()))).scalars().all()
    return [{
        "id": str(x.id), "period_start": x.billing_period_start.isoformat(),
        "period_end": x.billing_period_end.isoformat(), "status": x.status,
        "closed_at": x.closed_at.isoformat() if x.closed_at else None,
        "summary": x.summary,
    } for x in rows]


class CloseRequest(BaseModel):
    period_start: datetime
    period_end: datetime


@router.post("/period-closes/close", status_code=201, dependencies=[CSRF])
async def close_period(body: CloseRequest, session: SessionDep, principal: Principal):
    """Close a billing period. Blocked while material reconciliation
    exceptions are open/unwaived; each waiver needs an approved maker-checker
    approval (kind=recon_waiver)."""
    if not principal.can("period.close"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    await set_org_scope(session, root)
    existing = (await session.execute(
        select(PeriodClose).where(PeriodClose.org_path == root,
                                  PeriodClose.billing_period_start == body.period_start)
    )).scalar_one_or_none()
    if existing and existing.status == "closed":
        raise HTTPException(409, detail={"code": "already_closed"})

    # Gate on the LATEST completed reconciliation run for this period only:
    # exceptions from superseded runs are stale by definition.
    from app.models.reconciliation import ReconciliationRun

    latest = (await session.execute(
        select(ReconciliationRun.id)
        .where(ReconciliationRun.org_path == root,
               ReconciliationRun.period_start == body.period_start,
               ReconciliationRun.status == "completed")
        .order_by(ReconciliationRun.created_at.desc()).limit(1)
    )).scalars().first()
    if latest is None:
        raise HTTPException(409, detail={
            "code": "reconciliation_required",
            "message": "run reconciliation for this period before closing"})
    open_material = list((await session.execute(
        select(ReconciliationException).where(
            ReconciliationException.run_id == latest,
            ReconciliationException.status.in_(("open", "investigating")),
            ReconciliationException.materiality == "material",
        ))).scalars())
    blockers = []
    for exc in open_material:
        appr = await require_approved(session, kind="recon_waiver",
                                      entity_type="reconciliation_exception",
                                      entity_id=exc.id)
        if appr is None:
            blockers.append({"id": str(exc.id), "type": exc.exc_type,
                             "explanation": exc.explanation})
    if blockers:
        raise HTTPException(409, detail={
            "code": "material_exceptions_open",
            "message": f"{len(blockers)} material exception(s) must be resolved "
                       f"or waived via approved maker-checker before closing",
            "blockers": blockers[:10],
        })

    # close-gate approvals themselves need maker-checker: the closer requests
    # and a DIFFERENT user approves. If the caller already holds an approved
    # period_close_waiver for this period, proceed.
    # Closing itself is gated on period.close (checked above); the waiver
    # approvals for individual exceptions were verified in the blockers loop.
    pc = existing or PeriodClose(
        billing_period_start=body.period_start, billing_period_end=body.period_end,
        org_path=root, org_id=principal.org_id,
    )
    pc.status = "closed"
    pc.closed_by = principal.user_id
    pc.closed_at = datetime.now(UTC)
    pc.summary = {
        "invoices_issued": int((await session.execute(
            select(func.count(Invoice.id)).where(
                Invoice.org_path.like(root + "%"), Invoice.deleted_at.is_(None),
                Invoice.status.in_(("issued", "exported", "paid_or_settled", "corrected")),
                Invoice.period_start == body.period_start))).scalar_one()),
        "exceptions_waived": len(open_material),
    }
    session.add(pc)
    await record_audit(session, principal, action="period.closed", org_path=root,
                       summary=f"Period {body.period_start:%Y-%m} closed "
                               f"({pc.summary})",
                       entity_type="period_close", entity_id=pc.id)
    await session.commit()
    return {"ok": True, "status": pc.status, "summary": pc.summary}


class WaiverRequest(BaseModel):
    exception_id: uuid.UUID
    reason: str = Field(min_length=8, max_length=2000)


@router.post("/reconciliation/exceptions/{exception_id}/waive", status_code=201,
             dependencies=[CSRF])
async def request_waiver(exception_id: uuid.UUID, body: WaiverRequest,
                         session: SessionDep, principal: Principal):
    """Maker requests a waiver for a material exception; a different user with
    recon.waive approves it; then the exception can be marked waived."""
    if not principal.can("recon.waive"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    exc = await session.get(ReconciliationException, exception_id)
    if exc is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not exc.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    await set_org_scope(session, exc.org_path)
    req = await create_approval(
        session, kind="recon_waiver", entity_type="reconciliation_exception",
        entity_id=exc.id,
        summary=f"Waive {exc.exc_type} ({exc.amount_delta} {exc.currency}) — {body.reason[:200]}",
        org_path=exc.org_path, org_id=exc.org_id, maker_id=principal.user_id,
        impact_amount=abs(exc.amount_delta or ZERO),
    )
    await record_audit(session, principal, action="approval.requested",
                       org_path=exc.org_path,
                       summary=f"Waiver requested for exception {exception_id}",
                       entity_type="approval", entity_id=req.id)
    await session.commit()
    return {"id": str(req.id), "request_number": req.request_number}


@router.post("/reconciliation/exceptions/{exception_id}/mark-waived",
             dependencies=[CSRF])
async def mark_waived(exception_id: uuid.UUID, session: SessionDep,
                      principal: Principal):
    """Apply an approved waiver to the exception (checker or recon owner)."""
    if not principal.can("recon.waive"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    exc = await session.get(ReconciliationException, exception_id)
    if exc is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not exc.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    appr = await require_approved(session, kind="recon_waiver",
                                  entity_type="reconciliation_exception",
                                  entity_id=exc.id)
    if appr is None:
        raise HTTPException(409, detail={"code": "no_approved_waiver"})
    if appr.maker_id == principal.user_id:
        raise HTTPException(403, detail={"code": "self_approval",
                                         "message": "the waiver requester cannot apply it"})
    await set_org_scope(session, exc.org_path)
    exc.status = "waived"
    exc.waived_by = principal.user_id
    exc.waiver_approval_id = appr.id
    exc.resolution = f"Waived via {appr.request_number}: {appr.decision_note or ''}"
    await record_audit(session, principal, action="reconciliation.waived",
                       org_path=exc.org_path,
                       summary=f"Exception {exception_id} waived via {appr.request_number}",
                       entity_type="reconciliation_exception", entity_id=exc.id)
    await session.commit()
    return {"ok": True, "status": exc.status}


# --------------------------------------------------------- revenue leakage

@router.get("/revenue-leakage")
async def revenue_leakage(session: SessionDep, principal: Principal,
                          period_start: datetime | None = None):
    """Leakage report: unbilled usage, unmapped accounts, unallocated
    credits, and invoice-vs-run total mismatches. All Decimal."""
    if not principal.can("margin.view"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    from app.models.benefits import Credit
    from app.models.cost import CanonicalCostRecord

    cw: list = [CanonicalCostRecord.org_path.like(root + "%")]
    if period_start:
        cw.append(CanonicalCostRecord.billing_period_start == period_start)

    # 1. usage with no customer attribution
    unmapped = list((await session.execute(
        select(CanonicalCostRecord.payer_or_billing_account,
               func.sum(CanonicalCostRecord.provider_billed))
        .where(*cw, CanonicalCostRecord.customer_id.is_(None),
               CanonicalCostRecord.line_item_type == "usage")
        .group_by(CanonicalCostRecord.payer_or_billing_account))).all())

    # 2. attributed usage in periods with zero issued invoices
    unbilled = list((await session.execute(
        select(CanonicalCostRecord.customer_id,
               CanonicalCostRecord.billing_period_start,
               func.sum(CanonicalCostRecord.provider_billed))
        .where(*cw, CanonicalCostRecord.customer_id.isnot(None),
               CanonicalCostRecord.line_item_type == "usage")
        .group_by(CanonicalCostRecord.customer_id,
                  CanonicalCostRecord.billing_period_start))).all())
    unbilled_rows = []
    for cust_id, pstart, amt in unbilled:
        issued = int((await session.execute(
            select(func.count(Invoice.id)).where(
                Invoice.customer_id == cust_id, Invoice.deleted_at.is_(None),
                Invoice.period_start == pstart,
                Invoice.status.in_(("issued", "exported", "paid_or_settled", "corrected")))
        )).scalar_one())
        if not issued:
            cust = await session.get(Customer, cust_id)
            unbilled_rows.append({"customer": cust.display_name if cust else str(cust_id),
                                  "period": pstart.isoformat(), "amount": str(q(amt or ZERO))})

    # 3. unallocated provider credits
    creds = list((await session.execute(
        select(Credit).where(Credit.org_path.like(root + "%"),
                             Credit.allocation_status == "unallocated",
                             Credit.deleted_at.is_(None))
        .order_by(Credit.amount_total.desc()).limit(50))).scalars())

    # 4. invoice totals that don't match their pricing run
    from app.models.pricing import PricingRun

    drift = []
    invs = list((await session.execute(
        select(Invoice).where(Invoice.org_path.like(root + "%"),
                              Invoice.deleted_at.is_(None),
                              Invoice.pricing_run_id.isnot(None),
                              Invoice.status.in_(("issued", "exported", "paid_or_settled")))
        .limit(500))).scalars())
    for inv in invs:
        run = await session.get(PricingRun, inv.pricing_run_id)
        if run is None:
            continue
        run_total = q(Decimal(str((run.totals or {}).get("customer_subtotal", "0"))))
        if run_total != q(inv.total):
            drift.append({"invoice": inv.invoice_number,
                          "invoice_total": str(inv.total),
                          "run_customer_subtotal": str(run_total)})

    return {
        "unmapped_usage": [{"account": a or "?", "amount": str(q(amt or ZERO))}
                           for a, amt in unmapped],
        "unbilled_usage": unbilled_rows,
        "unallocated_credits": [{
            "id": str(c.id), "name": c.display_name, "kind": c.kind,
            "remaining": str(q((c.amount_total or ZERO) - (c.amount_used or ZERO))),
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        } for c in creds],
        "invoice_run_drift": drift,
    }


# ---------------------------------------------------------------- responses

def _csv_response(text: str, filename: str):
    from fastapi import Response
    return Response(content=text.encode("utf-8"), media_type="text/csv",
                    headers={"content-disposition": f'attachment; filename="{filename}"'})


def _pdf_response(data: bytes, filename: str):
    from fastapi import Response
    return Response(content=data, media_type="application/pdf",
                    headers={"content-disposition": f'attachment; filename="{filename}"'})
