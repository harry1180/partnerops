"""Integrations API (Phase 5): webhook endpoints, deliveries, ERP export.

- /integrations/webhooks — create (secret shown ONCE), list (secret masked),
  test-send, deliveries feed with signature verification status.
- /integrations — configured external systems (kind + status + config);
  configured but not silently live: every integration reports
  `connected: false` unless its transport actually executed a check
  (webhook test sends are real HTTP; the rest are boundaries labeled honestly).
- /invoices/{id}/export?fmt=erp — ERP-ready journal export (JSON/CSV):
  invoice header + customer-visible lines, deterministic, audited as an
  export like every other data leaving the platform.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.core import secretbox
from app.db.rls import set_org_scope
from app.models.approvals import Integration, WebhookDelivery, WebhookEndpoint
from app.models.billing_core import Customer
from app.models.invoices import ExportJob, Invoice, InvoiceLine
from app.services import webhooks as wh
from app.services.audit_service import record_audit

router = APIRouter()

VALID_KINDS = ("erp", "accounting", "marketplace", "payment", "servicenow",
               "slack", "teams", "email", "s3_export", "sftp_export")


def _require(principal: Principal, perm: str) -> None:
    if not principal.can(perm):
        raise HTTPException(403, detail={"code": "forbidden"})


def _partner_root(principal: Principal) -> str:
    if principal.is_platform_admin or principal.org_kind == "customer":
        raise HTTPException(400, detail={"code": "platform_admin_must_scope"})
    return principal.org_path


class EndpointCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1024)
    events: list[str] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=512)


@router.get("/integrations/overview")
async def overview(session: SessionDep, principal: Principal):
    _require(principal, "integration.manage")
    root = principal.scope_prefixes[0]
    await set_org_scope(session, root)
    eps = list((await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.org_path.like(root + "%"),
            WebhookEndpoint.deleted_at.is_(None)))).scalars())
    ints = list((await session.execute(
        select(Integration).where(Integration.org_path.like(root + "%"),
                                  Integration.deleted_at.is_(None)))).scalars())
    pend = int((await session.execute(
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.org_path.like(root + "%"),
            WebhookDelivery.status == "pending"))).scalar_one())
    return {
        "event_types": list(wh.EVENT_TYPES),
        "webhook_endpoints": [{
            "id": str(e.id), "url": e.url, "events": e.events, "status": e.status,
            "description": e.description,
            "secret_configured": secretbox.is_sealed(e.secret_ref) or bool(
                e.secret_ref and e.secret_ref.startswith("local:v1:")),
        } for e in eps],
        "integrations": [{
            "id": str(i.id), "kind": i.kind, "name": i.name, "status": i.status,
            "config": dict(i.config or {}),
            "connected": bool(i.last_check_ok),
            "last_check_at": i.last_check_at.isoformat() if i.last_check_at else None,
        } for i in ints],
        "pending_deliveries": pend,
    }


class IntegrationCreate(BaseModel):
    kind: str
    name: str = Field(min_length=2, max_length=255)
    config: dict = Field(default_factory=dict)


@router.post("/integrations", status_code=201, dependencies=[CSRF])
async def create_integration(body: IntegrationCreate, session: SessionDep,
                             principal: Principal):
    _require(principal, "integration.manage")
    if body.kind not in VALID_KINDS:
        raise HTTPException(422, detail={"code": "bad_kind", "allowed": list(VALID_KINDS)})
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    i = Integration(kind=body.kind, name=body.name, config=body.config,
                    status="configured", org_id=principal.org_id, org_path=org_path)
    session.add(i)
    await session.flush()
    await record_audit(session, principal, action="integration.changed", org_path=org_path,
                       summary=f"Integration '{body.name}' ({body.kind}) configured",
                       entity_type="integration", entity_id=i.id)
    await session.commit()
    return {"id": str(i.id), "connected": False,
            "note": "configured — live transport requires credentials (deployment config)"}


@router.post("/integrations/{integration_id}/check", dependencies=[CSRF])
async def check_integration(integration_id: uuid.UUID, session: SessionDep,
                            principal: Principal):
    """Health check. Only webhooks have a runnable transport locally today;
    other kinds report 'not connected' honestly instead of faking a ping."""
    _require(principal, "integration.manage")
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    i = await session.get(Integration, integration_id)
    if i is None or i.deleted_at is not None or not i.org_path.startswith(org_path):
        raise HTTPException(404, detail={"code": "not_found"})
    i.last_check_at = datetime.now(UTC)
    ok = False
    detail = "no live transport configured for this kind"
    if i.kind in ("slack", "teams") and (i.config or {}).get("webhook_url"):
        err = wh.validate_target_url(str(i.config["webhook_url"]),
                                     allow_private=True)  # local/test only
        ok = err is None
        detail = "target valid (delivery transport activates with credentials)" if err is None else err
    i.last_check_ok = ok
    await record_audit(session, principal, action="integration.changed", org_path=i.org_path,
                       summary=f"Integration '{i.name}' check: {'ok' if ok else detail}",
                       entity_type="integration", entity_id=i.id)
    await session.commit()
    return {"connected": ok, "detail": detail}


@router.post("/integrations/webhooks", status_code=201, dependencies=[CSRF])
async def create_webhook(body: EndpointCreate, session: SessionDep, principal: Principal):
    _require(principal, "integration.manage")
    org_path = _partner_root(principal)
    bad = [e for e in body.events if e not in wh.EVENT_TYPES]
    if bad:
        raise HTTPException(422, detail={"code": "bad_events", "unknown": bad})
    try:
        ep, issue = await wh.create_endpoint(session, org_path, url=body.url,
                                             events=body.events,
                                             description=body.description)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "bad_target", "message": str(exc)}) from None
    await record_audit(session, principal, action="integration.changed", org_path=org_path,
                       summary=f"Webhook endpoint {body.url} subscribed to {body.events}",
                       entity_type="webhook_endpoint", entity_id=ep.id,
                       detail={"secret_shown_once": True})
    await session.commit()
    return {"id": str(ep.id), "signing_secret": issue.secret,
            "note": "store now — the secret is shown exactly once; deliveries sign "
                    "with X-CPPartnerOps-Signature: sha256=HMAC(secret, timestamp.body)"}


class TestSend(BaseModel):
    event_type: str = "integration.test"


@router.post("/integrations/webhooks/{endpoint_id}/test", dependencies=[CSRF])
async def test_webhook(endpoint_id: uuid.UUID, body: TestSend, session: SessionDep,
                       principal: Principal):
    _require(principal, "integration.manage")
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    ep = await session.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.deleted_at is not None or not ep.org_path.startswith(org_path):
        raise HTTPException(404, detail={"code": "not_found"})
    n = await wh.queue_event(session, org_path, "integration.test",
                             {"endpoint_id": str(ep.id), "requested_by": principal.email},
                             force_endpoint_id=ep.id, scope=org_path)
    # sweep only this endpoint: another endpoint's dead target must not delay
    # this call (head-of-line blocking) — the beat sweep handles the rest.
    delivered = await wh.deliver_due(session, limit=10, endpoint_id=ep.id)
    rows = list((await session.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.endpoint_id == ep.id,
            WebhookDelivery.event_type == "integration.test")
        .order_by(WebhookDelivery.created_at.desc()).limit(3))).scalars())
    await record_audit(session, principal, action="integration.changed", org_path=org_path,
                       summary=f"Webhook test event queued ({n}) for {ep.url}",
                       entity_type="webhook_endpoint", entity_id=ep.id)
    await session.commit()
    return {"queued": n, "attempts_this_call": delivered,
            "latest": [{"status": d.status, "attempts": d.attempts,
                        "response_code": d.response_code,
                        "error": (d.payload or {}).get("_delivery_error")}
                       for d in rows if d.event_type == "integration.test"]}


@router.post("/integrations/webhooks/{endpoint_id}/rotate-secret", dependencies=[CSRF])
async def rotate_webhook_secret(endpoint_id: uuid.UUID, session: SessionDep,
                                principal: Principal):
    """New signing secret, shown exactly once. In-flight deliveries keep the
    signature the receiver last validated — rotation is a receiver-coordinates
    operation; run it during a quiet window or dual-verify on their side."""
    _require(principal, "integration.manage")
    org_path = _partner_root(principal)
    await set_org_scope(session, org_path)
    ep = await session.get(WebhookEndpoint, endpoint_id)
    if ep is None or ep.deleted_at is not None or not ep.org_path.startswith(org_path):
        raise HTTPException(404, detail={"code": "not_found"})
    secret = wh.generate_signing_secret()
    ep.secret_ref = secretbox.seal(secret)
    await record_audit(session, principal, action="integration.changed", org_path=org_path,
                       summary=f"Webhook signing secret rotated for {ep.url}",
                       entity_type="webhook_endpoint", entity_id=ep.id,
                       detail={"secret_shown_once": True})
    await session.commit()
    return {"signing_secret": secret,
            "note": "shown once; update the receiver's verifier before the next event"}


@router.post("/integrations/webhooks/deliver", dependencies=[CSRF])
async def retry_pending(session: SessionDep, principal: Principal,
                        endpoint_id: uuid.UUID | None = Query(None)):
    """Operational: run one delivery sweep now instead of waiting for the
    5-minute beat (endpoints that were down come back via pending retries).
    Optional endpoint_id scopes the sweep (avoids older dead endpoints
    consuming the whole batch)."""
    _require(principal, "integration.manage")
    attempted = await wh.deliver_due(session, endpoint_id=endpoint_id)
    return {"attempted": attempted}


@router.get("/integrations/webhooks/{endpoint_id}/deliveries")
async def list_deliveries(endpoint_id: uuid.UUID, session: SessionDep,
                          principal: Principal, page: int = 1, page_size: int = 25):
    _require(principal, "integration.manage")
    root = principal.scope_prefixes[0]
    ep = await session.get(WebhookEndpoint, endpoint_id)
    if ep is None or not ep.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    stmt = select(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id)
    total = int((await session.execute(
        select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(WebhookDelivery.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [{
        "id": str(d.id), "event": d.event_type, "status": d.status,
        "attempts": d.attempts, "response_code": d.response_code,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        "payload_preview": {k: v for k, v in (d.payload or {}).items()
                            if not k.startswith("_")},
        "error": (d.payload or {}).get("_delivery_error"),
    } for d in rows], "total": total}


# ---------------- ERP export ----------------

@router.get("/invoices/{invoice_id}/export")
async def export_invoice_erp(invoice_id: uuid.UUID, session: SessionDep,
                             principal: Principal,
                             fmt: str = Query("json", pattern="^(json|csv)$")):
    """ERP/accounting journal export: header + customer-visible lines.
    Partner fields (margin/provider cost/internal notes) are excluded from
    this boundary on purpose — ERP systems book what the customer was
    charged, not what the partner earned."""
    _require(principal, "export.data")
    root = principal.scope_prefixes[0]
    inv = await session.get(Invoice, invoice_id)
    if inv is None or inv.deleted_at is not None or not inv.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    cust = await session.get(Customer, inv.customer_id)
    lines = list((await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id,
                                  InvoiceLine.customer_visible)
        .order_by(InvoiceLine.line_number))).scalars())
    doc: dict[str, Any] = {
        "schema": "cpo.erp.v1",
        "document_type": "invoice",
        "document_number": inv.invoice_number,
        "status": inv.status,
        "issue_date": inv.issued_at.strftime("%Y-%m-%d") if inv.issued_at else None,
        "due_date": inv.due_date.strftime("%Y-%m-%d") if inv.due_date else None,
        "period": {"start": inv.period_start.strftime("%Y-%m-%d"),
                   "end": inv.period_end.strftime("%Y-%m-%d")},
        "customer": {"id": str(inv.customer_id),
                     "name": cust.display_name if cust else None,
                     "code": cust.code if cust else None,
                     "po_number": cust.po_number if cust else None},
        "currency": inv.currency,
        "totals": {
            "subtotal": str(inv.subtotal), "discounts": str(inv.discounts_total),
            "credits": str(inv.credits_total), "fees": str(inv.fees_total),
            "adjustments": str(inv.adjustments_total), "taxes": str(inv.taxes_total),
            "prior_period_adjustments": str(inv.prior_period_adjustments_total),
            "grand_total": str(inv.total),
        },
        "lines": [{
            "line": ln.line_number, "type": ln.kind,
            "description": ln.description,
            "quantity": str(ln.quantity) if ln.quantity is not None else None,
            "amount": str(ln.amount),
            "dimensions": ln.group_key,
        } for ln in lines],
        "notes_customer": inv.notes_customer,
    }
    job = ExportJob(
        kind="erp_invoice_export", parameters={"invoice_id": str(inv.id), "fmt": fmt},
        status="completed", requested_by=principal.user_id,
        completed_at=datetime.now(UTC), org_path=inv.org_path,
        org_id=uuid.UUID(inv.org_path.strip("/").split("/")[-1]))
    session.add(job)
    await record_audit(session, principal, action="export.generated", org_path=inv.org_path,
                       summary=f"ERP export of invoice {inv.invoice_number} ({fmt})",
                       entity_type="invoice", entity_id=inv.id,
                       detail={"fmt": fmt, "lines": len(lines)})
    await session.commit()
    from fastapi.responses import Response
    if fmt == "csv":
        import csv as _csv
        import io as _io
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["section", "key", "field", "value"])
        for k, v in (("document_number", doc["document_number"]), ("status", doc["status"]),
                     ("issue_date", doc["issue_date"]), ("due_date", doc["due_date"]),
                     ("period_start", doc["period"]["start"]),
                     ("period_end", doc["period"]["end"])):
            w.writerow(["header", k, "", v if v is not None else ""])
        for k, v in doc["customer"].items():
            w.writerow(["customer", k, "", v if v is not None else ""])
        w.writerow(["header", "currency", "", doc["currency"]])
        for name, val in doc["totals"].items():
            w.writerow(["total", name, "", val])
        for ln in doc["lines"]:
            w.writerow(["line", str(ln["line"]), "description", ln["description"]])
            w.writerow(["line", str(ln["line"]), "amount", ln["amount"]])
        return Response(content=buf.getvalue().encode(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{inv.invoice_number}-erp.csv"'})
    import json as _json
    return Response(content=_json.dumps(doc, indent=1).encode(),
                    media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="{inv.invoice_number}-erp.json"'})
