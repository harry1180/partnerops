"""Billing pipeline API: ingestion, pricing, invoices, reconciliation.

Everything here is scoped: files/customers/pricing belong to the caller's
subtree; issued invoices are immutable; heavy ops are jobs when queued but
run inline for demo-scale payloads (Phase 6 adds queue-offload thresholds).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CSRF, Principal, SessionDep, get_principal  # noqa: F401
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord, RawBillingFile
from app.models.invoices import Invoice, InvoiceLine
from app.models.pricing import PricingRun, PricingRunItem
from app.models.reconciliation import ReconciliationException
from app.services import (
    document_service,
    ingest_service,
    invoice_lifecycle,
    invoice_service,
    pricing_service,
    reconciliation_service,
)
from app.services.audit_service import record_audit

router = APIRouter()


async def _scoped_customer(session: AsyncSession, principal: Principal,
                           customer_id: uuid.UUID, permission: str) -> Customer:
    if not principal.can(permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    c = await session.get(Customer, customer_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not c.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    return c


async def _owner_org_for_upload(session: AsyncSession, principal: Principal) -> tuple[uuid.UUID, str]:
    if principal.is_platform_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail={"code": "platform_admin_must_scope",
                                    "message": "sign in with the partner org that receives the file"})
    org_id = principal.org_id
    org_path = principal.org_path
    return org_id, org_path


# ------------------------------------------------------------------ ingest

async def _ingest_upload(
    session: AsyncSession, principal: Principal, file: UploadFile,
    object_prefix: str, provider_code: str,
) -> dict:
    """Shared body for /ingestion/{provider}/upload."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    org_id, org_path = await _owner_org_for_upload(session, principal)
    content = await file.read()
    if len(content) > 64 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail={"code": "too_large"})
    if not content:
        raise HTTPException(400, detail={"code": "empty_file"})

    key = f"{object_prefix}/{file.filename or 'file.csv'}"
    stored_key = key
    try:
        from app.services.storage import get_storage

        get_storage().put_bytes(key, content, "text/csv")
    except Exception:
        # object storage unavailable (local smoke): ingest from memory, keep key
        stored_key = f"inline://{file.filename or 'file.csv'}"
    import hashlib

    sha256 = hashlib.sha256(content).hexdigest()

    summary = await ingest_service.ingest_csv(
        session, org_id=org_id, org_path=org_path, provider_code=provider_code,
        parser_version=1, filename=file.filename or "file.csv",
        body=content, object_key=stored_key, source=f"{provider_code}_manual_upload",
        correlation_id=correlation_id_var_safe(), actor_user_id=principal.user_id,
    )
    return {
        "file_id": str(summary.file_id), "status": summary.status,
        "rows": summary.rows, "canonical": summary.canonical,
        "duplicates": summary.duplicates, "quarantined": summary.quarantined,
        "unmapped_accounts": summary.unmapped_accounts,
        "skipped_duplicate_file": summary.skipped_duplicate_file,
        "sha256": sha256[:16],
    }


@router.post("/ingestion/aws/upload", status_code=201, dependencies=[CSRF])
async def upload_aws_file(
    session: SessionDep,
    principal: Principal,
    file: UploadFile = File(...),
    object_prefix: str = Form("raw/aws/upload"),
):
    return await _ingest_upload(session, principal, file, object_prefix, "aws")


@router.post("/ingestion/azure/upload", status_code=201, dependencies=[CSRF])
async def upload_azure_file(
    session: SessionDep,
    principal: Principal,
    file: UploadFile = File(...),
    object_prefix: str = Form("raw/azure/upload"),
):
    return await _ingest_upload(session, principal, file, object_prefix, "azure")


@router.post("/ingestion/synthetic/load", status_code=201, dependencies=[CSRF])
async def load_synthetic(
    session: SessionDep,
    principal: Principal,
    months: str | None = Form(None),  # comma list e.g. 2026-06,2026-07
):
    """Load the deterministic demo CUR files (fixtures) into the caller's org.

    This is the "import synthetic AWS billing data" step of the demo without
    requiring object storage or manual uploads."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    org_id, org_path = await _owner_org_for_upload(session, principal)
    from app.ingestion.synthetic_aws import build_all

    months_data = build_all()["777700000001"]
    wanted = [m.strip() for m in months.split(",")] if months else sorted(months_data)
    results = []
    for label in wanted:
        if label not in months_data:
            raise HTTPException(400, detail={"code": "bad_month", "value": label})
        summary = await ingest_service.ingest_csv(
            session, org_id=org_id, org_path=org_path, provider_code="aws",
            parser_version=1, filename=f"{label}.csv",
            body=months_data[label].encode(),
            object_key=f"fixtures/aws/777700000001/{label}.csv",
            source="synthetic", correlation_id=correlation_id_var_safe(),
            actor_user_id=principal.user_id,
        )
        results.append({
            "month": label, "file_id": str(summary.file_id), "status": summary.status,
            "canonical": summary.canonical, "duplicates": summary.duplicates,
            "quarantined": summary.quarantined,
            "unmapped_accounts": summary.unmapped_accounts,
            "skipped_duplicate_file": summary.skipped_duplicate_file,
        })
    return {"results": results}


@router.post("/ingestion/synthetic/azure/load", status_code=201, dependencies=[CSRF])
async def load_synthetic_azure(
    session: SessionDep,
    principal: Principal,
    months: str | None = Form(None),
):
    """Load the deterministic Azure fixtures (see synthetic_azure.py)."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    org_id, org_path = await _owner_org_for_upload(session, principal)
    from app.ingestion.synthetic_azure import BILLING_PROFILE, build_all

    months_data = build_all()[BILLING_PROFILE]
    wanted = [m.strip() for m in months.split(",")] if months else sorted(months_data)
    results = []
    for label in wanted:
        if label not in months_data:
            raise HTTPException(400, detail={"code": "bad_month", "value": label})
        summary = await ingest_service.ingest_csv(
            session, org_id=org_id, org_path=org_path, provider_code="azure",
            parser_version=1, filename=f"{label}.csv",
            body=months_data[label].encode(),
            object_key=f"fixtures/azure/{BILLING_PROFILE}/{label}.csv",
            source="synthetic", correlation_id=correlation_id_var_safe(),
            actor_user_id=principal.user_id,
        )
        results.append({
            "month": label, "file_id": str(summary.file_id), "status": summary.status,
            "canonical": summary.canonical, "duplicates": summary.duplicates,
            "quarantined": summary.quarantined,
            "unmapped_accounts": summary.unmapped_accounts,
            "skipped_duplicate_file": summary.skipped_duplicate_file,
        })
    return {"results": results}


@router.get("/ingestion/files")
async def list_files(session: SessionDep, principal: Principal):
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    files = (
        await session.execute(
            select(RawBillingFile).where(RawBillingFile.org_path.like(root + "%"))
            .order_by(RawBillingFile.created_at.desc()).limit(100)
        )
    ).scalars().all()
    return [{
        "id": str(f.id), "filename": f.original_filename, "provider": f.provider_code,
        "status": f.status, "rows": f.row_count, "source": f.source,
        "sha256": f.sha256[:16], "period_start": f.billing_period_start.isoformat()
        if f.billing_period_start else None,
        "parser_version": f.parser_version,
        "ingested_at": f.created_at.isoformat(),
    } for f in files]


# ------------------------------------------------------------------ pricing

class PricingRequest(BaseModel):
    customer_id: uuid.UUID
    contract_version_id: uuid.UUID
    period_start: datetime
    period_end: datetime


@router.post("/pricing/runs", status_code=202, dependencies=[CSRF])
async def start_pricing_run(body: PricingRequest, session: SessionDep,
                            principal: Principal):
    await _scoped_customer(session, principal, body.customer_id, "pricing.run")
    try:
        result = await pricing_service.run_pricing(
            session, customer_id=body.customer_id,
            contract_version_id=body.contract_version_id,
            period_start=body.period_start, period_end=body.period_end,
            actor_user_id=principal.user_id,
            correlation_id=correlation_id_var_safe(),
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "pricing_error", "message": str(exc)}) from None
    return {"run_id": str(result.run_id), "status": result.status, "totals": result.totals}


@router.get("/pricing/runs/{run_id}")
async def get_pricing_run(run_id: uuid.UUID, session: SessionDep, principal: Principal):
    run = await session.get(PricingRun, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "not_found"})
    await _scoped_customer(session, principal, run.customer_id, "pricing.run")
    items = (
        await session.execute(
            select(PricingRunItem).where(PricingRunItem.run_id == run.id)
            .order_by(PricingRunItem.item_number)
        )
    ).scalars().all()
    return {
        "id": str(run.id), "status": run.status, "run_number": run.run_number,
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "engine_version": run.engine_version, "totals": run.totals,
        "supersedes_id": str(run.supersedes_id) if run.supersedes_id else None,
        "items": [{
            "item_number": i.item_number, "group_key": i.group_key,
            "group_label": i.group_label, "line_kind": i.line_kind,
            "input_amount": str(i.input_amount) if i.input_amount is not None else None,
            "output_amount": str(i.output_amount),
            "provider_cost_amount": str(i.provider_cost_amount),
            "formula": i.formula, "rule_version_ids": i.rule_version_ids,
            "trace": i.calculation_trace,
        } for i in items],
    }


# ------------------------------------------------------------------ invoices

class InvoiceCreate(BaseModel):
    run_id: uuid.UUID


@router.post("/invoices", status_code=201, dependencies=[CSRF])
async def create_invoice(body: InvoiceCreate, session: SessionDep, principal: Principal):
    run = await session.get(PricingRun, body.run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "not_found"})
    await _scoped_customer(session, principal, run.customer_id, "invoice.write")
    try:
        invoice = await invoice_service.build_invoice_from_run(
            session, run_id=run.id, principal_org_path=run.org_path,
            actor_user_id=principal.user_id, correlation_id=correlation_id_var_safe(),
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "invoice_error", "message": str(exc)}) from None
    return {"id": str(invoice.id), "invoice_number": invoice.invoice_number,
            "status": invoice.status, "total": str(invoice.total),
            "currency": invoice.currency}


@router.get("/invoices")
async def list_invoices(session: SessionDep, principal: Principal,
                        customer_id: uuid.UUID | None = None,
                        status_: str | None = Query(None, alias="status"),
                        page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    if not principal.can("invoice.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    stmt = select(Invoice).where(Invoice.deleted_at.is_(None),
                                 Invoice.org_path.like(root + "%"))
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if status_:
        stmt = stmt.where(Invoice.status == status_)
    total = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(Invoice.period_start.desc(), Invoice.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [{
            "id": str(i.id), "invoice_number": i.invoice_number,
            "customer_id": str(i.customer_id), "status": i.status,
            "total": str(i.total), "currency": i.currency,
            "period_start": i.period_start.isoformat(),
            "period_end": i.period_end.isoformat(),
            "margin_total": str(i.margin_total) if principal.can("margin.view") else None,
        } for i in rows],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, session: SessionDep, principal: Principal):
    inv = await session.get(Invoice, invoice_id)
    if inv is None or inv.deleted_at is not None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    is_customer_role = "margin.view" not in principal.permissions
    if not principal.is_platform_admin and not inv.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    lines = (
        await session.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
            .order_by(InvoiceLine.line_number)
        )
    ).scalars().all()
    visible = [ln for ln in lines if ln.customer_visible or not is_customer_role]
    return {
        "id": str(inv.id), "invoice_number": inv.invoice_number,
        "customer_id": str(inv.customer_id), "status": inv.status,
        "currency": inv.currency, "payment_terms": inv.payment_terms,
        "period_start": inv.period_start.isoformat(),
        "period_end": inv.period_end.isoformat(),
        "subtotal": str(inv.subtotal), "discounts_total": str(inv.discounts_total),
        "credits_total": str(inv.credits_total), "fees_total": str(inv.fees_total),
        "adjustments_total": str(inv.adjustments_total),
        "taxes_total": str(inv.taxes_total),
        "prior_period_adjustments_total": str(inv.prior_period_adjustments_total),
        "total": str(inv.total),
        # partner economics — NEVER present for customer-scoped users
        "provider_cost_total": str(inv.provider_cost_total) if not is_customer_role else None,
        "margin_total": str(inv.margin_total) if not is_customer_role else None,
        "notes_customer": inv.notes_customer,
        "notes_internal": inv.notes_internal if not is_customer_role else None,
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "lines": [{
            "line_number": ln.line_number, "kind": ln.kind,
            "group_key": ln.group_key, "description": ln.description,
            "quantity": str(ln.quantity) if ln.quantity is not None else None,
            "amount": str(ln.amount), "customer_visible": ln.customer_visible,
            "source_record_ids": ln.source_record_ids,
            "rule_version_ids": ln.rule_version_ids,
            "pricing_run_item_id": str(ln.pricing_run_item_id) if ln.pricing_run_item_id else None,
        } for ln in visible],
    }


class TransitionRequest(BaseModel):
    to_status: str
    note: str | None = None


@router.post("/invoices/{invoice_id}/transition", dependencies=[CSRF])
async def transition(invoice_id: uuid.UUID, body: TransitionRequest,
                     session: SessionDep, principal: Principal):
    inv = await session.get(Invoice, invoice_id)
    if inv is None or inv.deleted_at is not None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not inv.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    try:
        await invoice_lifecycle.transition_invoice(
            session, inv, body.to_status, principal, note=body.note)
        await session.commit()
    except invoice_lifecycle.LifecycleError as exc:
        await session.rollback()
        code = exc.code
        http = status.HTTP_403_FORBIDDEN if code == "forbidden" else status.HTTP_409_CONFLICT
        raise HTTPException(http, detail={"code": code, "message": str(exc)}) from None
    return {"id": str(inv.id), "status": inv.status}


@router.get("/invoices/{invoice_id}/download")
async def download_invoice(invoice_id: uuid.UUID, session: SessionDep,
                           principal: Principal, fmt: str = Query("pdf")):
    if not principal.can("invoice.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    inv = await session.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not inv.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    customer = await session.get(Customer, inv.customer_id)
    lines = list((
        await session.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
            .order_by(InvoiceLine.line_number)
        )
    ).scalars())
    assert customer is not None
    if fmt == "csv":
        data = document_service.render_invoice_csv(inv, customer, lines)
        media = "text/csv"
        fname = f"{inv.invoice_number}.csv"
    else:
        from app.api.v1.branding import _resolve_branding

        cfg = await _resolve_branding(session, inv.org_path)
        branding = {
            "product_name": cfg.product_name, "primary_color": cfg.primary_color,
            "support_email": cfg.support_email,
        } if cfg else {}
        data = document_service.render_invoice_pdf(inv, customer, lines, branding)
        media = "application/pdf"
        fname = f"{inv.invoice_number}.pdf"
    if principal.can("export.data"):
        await record_audit(session, principal, action="export.data", org_path=inv.org_path,
                           summary=f"Invoice {inv.invoice_number} downloaded ({fmt})",
                           entity_type="invoice", entity_id=inv.id)
        await session.commit()
    from fastapi.responses import Response

    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ------------------------------------------------------------------ recon

class ReconRequest(BaseModel):
    period_start: datetime
    period_end: datetime
    tolerance_abs: str = "0.000001"


@router.post("/reconciliation/runs", status_code=201, dependencies=[CSRF])
async def create_recon_run(body: ReconRequest, session: SessionDep, principal: Principal):
    if not principal.can("recon.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    if principal.is_platform_admin:
        raise HTTPException(400, detail={"code": "platform_admin_must_scope"})
    result = await reconciliation_service.run_reconciliation(
        session, org_path=principal.org_path, period_start=body.period_start,
        period_end=body.period_end, principal=principal,
        tolerance_abs=Decimal(body.tolerance_abs),
        correlation_id=correlation_id_var_safe(),
    )
    return {"run_id": str(result.run_id), "exceptions_open": result.exceptions_open,
            "material_open": result.material_open, "summary": result.summary}


@router.get("/reconciliation/bill-totals")
async def list_bill_totals(session: SessionDep, principal: Principal,
                           period_start: datetime | None = None):
    """Provider bill totals in scope: invoice-level statements plus the
    per-cloud-account rollups recorded at ingestion (Phase 3 grain)."""
    if not principal.can("recon.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    from app.models.reconciliation import ProviderBillTotal

    root = principal.scope_prefixes[0]
    stmt = select(ProviderBillTotal).where(ProviderBillTotal.org_path.like(root + "%"))
    if period_start:
        stmt = stmt.where(ProviderBillTotal.period_start == period_start)
    rows = (await session.execute(
        stmt.order_by(ProviderBillTotal.period_start.desc(),
                      ProviderBillTotal.provider_code, ProviderBillTotal.level)
        .limit(300))).scalars().all()
    return {"items": [{
        "id": str(t.id), "provider": t.provider_code, "level": t.level,
        "billing_account_ref": t.billing_account_ref,
        "period_start": t.period_start.isoformat(),
        "billed_total": str(t.billed_total), "currency": t.currency,
        "evidence": t.evidence,
    } for t in rows]}


@router.get("/reconciliation/exceptions")
async def list_exceptions(session: SessionDep, principal: Principal,
                          status_: str | None = Query(None, alias="status"),
                          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    if not principal.can("recon.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    stmt = select(ReconciliationException).where(
        ReconciliationException.org_path.like(root + "%"))
    if status_:
        stmt = stmt.where(ReconciliationException.status == status_)
    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(ReconciliationException.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [{
        "id": str(e.id), "type": e.exc_type, "materiality": e.materiality,
        "severity": e.severity, "status": e.status, "amount_delta": str(e.amount_delta),
        "explanation": e.explanation, "customer_id": str(e.customer_id) if e.customer_id else None,
        "billing_account_ref": e.billing_account_ref, "evidence": e.evidence,
        "run_id": str(e.run_id),
    } for e in rows], "total": total, "page": page, "page_size": page_size}


class ResolveRequest(BaseModel):
    status: str
    resolution: str | None = None


@router.patch("/reconciliation/exceptions/{exc_id}", dependencies=[CSRF])
async def resolve_exception(exc_id: uuid.UUID, body: ResolveRequest,
                            session: SessionDep, principal: Principal):
    if body.status not in ("investigating", "resolved"):
        raise HTTPException(400, detail={"code": "bad_status"})
    exc = await session.get(ReconciliationException, exc_id)
    if exc is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not exc.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    if body.status == "resolved" and not principal.can("recon.resolve"):
        raise HTTPException(403, detail={"code": "forbidden"})
    exc.status = body.status
    if body.status == "resolved":
        exc.resolution = body.resolution
        exc.resolved_at = datetime.now(UTC)
    exc.notes = (exc.notes or "") + (f"\n[{principal.email}] {body.resolution}" if body.resolution else "")
    await record_audit(session, principal, action="reconciliation.exception_updated",
                       org_path=exc.org_path,
                       summary=f"Reconciliation exception {exc.exc_type} → {body.status}",
                       entity_type="reconciliation_exception", entity_id=exc.id)
    await session.commit()
    return {"ok": True, "status": exc.status}


# ------------------------------------------------------------------ dq / usage

@router.get("/data-quality")
async def data_quality(session: SessionDep, principal: Principal,
                       period_start: datetime | None = None):
    """Dashboard feed: missing periods, mapped, duplicates, quarantine."""
    if not principal.can("recon.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]

    from app.models.cost import QuarantinedRecord

    files = (await session.execute(
        select(RawBillingFile).where(RawBillingFile.org_path.like(root + "%"))
    )).scalars().all()
    periods = sorted({f.billing_period_start for f in files if f.billing_period_start})
    failed = [f for f in files if f.status in ("failed", "quarantined")]
    unmapped = list((await session.execute(
        select(CanonicalCostRecord.payer_or_billing_account,
               func.sum(CanonicalCostRecord.provider_billed))
        .where(CanonicalCostRecord.org_path.like(root + "%"),
               CanonicalCostRecord.customer_id.is_(None))
        .group_by(CanonicalCostRecord.payer_or_billing_account)
    )).all())
    quar = (await session.execute(
        select(QuarantinedRecord.reason, func.count(QuarantinedRecord.id))
        .where(QuarantinedRecord.org_path.like(root + "%"),
               QuarantinedRecord.status == "open")
        .group_by(QuarantinedRecord.reason)
    )).all()
    missing = []
    for p in periods:
        cnt = (await session.execute(
            select(func.count(RawBillingFile.id)).where(
                RawBillingFile.org_path.like(root + "%"),
                RawBillingFile.billing_period_start == p,
                RawBillingFile.status == "parsed")
        )).scalar_one()
        if cnt == 0:
            missing.append(p.isoformat())
    return {
        "billing_periods_present": [p.isoformat() for p in periods],
        "missing_parsed_files_periods": missing,
        "failed_jobs": [{"file_id": str(f.id), "filename": f.original_filename,
                         "status": f.status, "error": (f.error_message or "")[:200]}
                        for f in failed],
        "unmapped_usage": [{"account": a or "?", "amount": str(m or 0)} for a, m in unmapped],
        "quarantined_by_reason": {r: int(c) for r, c in quar},
    }


@router.get("/usage/explorer")
async def usage_explorer(session: SessionDep, principal: Principal,
                         period_start: datetime | None = None,
                         group_by: str = Query("service"),
                         page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    """Server-side grouped usage — never ships raw records to the browser."""
    if not principal.can("cost.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    allowed_groups = {"service", "cost_category", "region", "environment", "application", "owner"}
    col = getattr(CanonicalCostRecord, group_by if group_by in allowed_groups else "service")
    root = principal.scope_prefixes[0]
    stmt = select(
        col.label("grp"),
        func.sum(CanonicalCostRecord.provider_billed).label("provider_cost"),
        func.sum(CanonicalCostRecord.list_cost).label("list_cost"),
        func.sum(CanonicalCostRecord.credit).label("credit"),
        func.count(CanonicalCostRecord.id).label("rows"),
    ).where(
        CanonicalCostRecord.org_path.like(root + "%"),
        CanonicalCostRecord.line_item_type == "usage",
    ).group_by("grp").order_by(func.sum(CanonicalCostRecord.provider_billed).desc())
    if period_start:
        stmt = stmt.where(CanonicalCostRecord.billing_period_start == period_start)
    total_groups = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()
    return {"group_by": group_by if group_by in allowed_groups else "service",
            "items": [{
                "group": str(grp), "provider_cost": str(cost or 0),
                "list_cost": str(lst or 0), "credit": str(cr or 0), "rows": int(rc),
            } for grp, cost, lst, cr, rc in rows],
            "total": total_groups, "page": page, "page_size": page_size}


def correlation_id_var_safe() -> str:
    from app.core.logging import correlation_id_var

    return correlation_id_var.get() or uuid.uuid4().hex[:16]
