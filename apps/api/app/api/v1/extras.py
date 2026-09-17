"""Rule Test sandbox + disputes (both needed for the Phase 1 journey).

Sandbox: run one rule version against historical canonical data WITHOUT
persisting pricing runs; stores evidence in rule_sandbox_tests so the number
shown next to "publish" is reproducible and auditable.

Disputes: portal customers open them against issued invoices; partner staff
investigate and resolve (never by editing the immutable invoice).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.engine.money import ZERO, q
from app.engine.rules import EffectiveRule, apply_rules
from app.models.billing_core import AccountFamily, CloudAccount, Customer
from app.models.contracts import BillingRule, BillingRuleVersion
from app.models.cost import CanonicalCostRecord
from app.models.invoices import Dispute, Invoice
from app.services.audit_service import record_audit
from app.services.pricing_service import _aggregate

router = APIRouter()


class SandboxRequest(BaseModel):
    period_start: datetime
    period_end: datetime
    customer_id: uuid.UUID | None = None


class SandboxResponse(BaseModel):
    baseline_total: str
    sandbox_total: str
    delta: str
    delta_pct: str | None
    affected_groups: int
    groups_preview: list[dict]
    rule_hits: int
    test_id: str


@router.post("/billing-rule-versions/{version_id}/sandbox", response_model=SandboxResponse,
             dependencies=[CSRF])
async def sandbox_test_rule(version_id: uuid.UUID, body: SandboxRequest,
                            session: SessionDep, principal: Principal):
    """Preview the financial effect of a rule version on historical usage."""
    if not principal.can("rule.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    rv = await session.get(BillingRuleVersion, version_id)
    if rv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not rv.org_path.startswith(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    rule = await session.get(BillingRule, rv.rule_id)
    assert rule is not None
    await set_org_scope(session, rv.org_path)

    base_filters = [
        CanonicalCostRecord.billing_period_start == body.period_start,
        CanonicalCostRecord.billing_period_end == body.period_end,
        CanonicalCostRecord.line_item_type == "usage",
    ]
    if body.customer_id:
        base_filters.append(CanonicalCostRecord.customer_id == body.customer_id)
    rows = list((await session.execute(
        select(CanonicalCostRecord).where(*base_filters)
    )).scalars())
    if not rows:
        raise HTTPException(400, detail={"code": "no_history",
                                         "message": "no canonical usage in that period"})
    slices = _aggregate(rows, ["service"])
    baseline = apply_rules(slices, [], "provider_billed")
    er = EffectiveRule(
        rule_id=str(rule.id), version_id=str(rv.id), code=rule.code, name=rule.name,
        rule_type=rule.rule_type, priority=rv.priority, calc_order=rv.calc_order,
        applied_basis=rv.applied_basis, filters=rv.filters or {},
        parameters=rv.parameters or {}, version_number=rv.version_number,
    )
    sandboxed = apply_rules(slices, [er], "provider_billed")
    base_total = sum((p.customer_amount for p in baseline), ZERO)
    new_total = sum((p.customer_amount for p in sandboxed), ZERO)
    hits = sum(1 for p in sandboxed if p.trace)
    preview = [
        {"group": p.slice.label, "before": str(q(p.slice.provider_billed)),
         "after": str(q(p.customer_amount)), "rules_applied": len(p.trace)}
        for p in sandboxed[:10]
    ]
    from app.models.contracts import RuleSandboxTest

    test = RuleSandboxTest(
        rule_id=rule.id, rule_version_id=rv.id, tested_by=principal.user_id,
        period_start=body.period_start, period_end=body.period_end,
        input_summary={"rows": len(rows), "baseline_total": str(base_total)},
        result_summary={"sandbox_total": str(new_total), "delta": str(new_total - base_total),
                        "rule_hits": hits},
        engine_version="sandbox-0.1.0",
        org_id=rv.org_id, org_path=rv.org_path,
    )
    session.add(test)
    await session.commit()
    delta = new_total - base_total
    return SandboxResponse(
        baseline_total=str(q(base_total)), sandbox_total=str(q(new_total)),
        delta=str(q(delta)),
        delta_pct=str(q(delta / base_total * 100)) if base_total else None,
        affected_groups=hits, groups_preview=preview, rule_hits=hits,
        test_id=str(test.id),
    )


# --------------------------------------------------------------- disputes

class DisputeCreate(BaseModel):
    invoice_id: uuid.UUID
    subject: str = Field(min_length=4, max_length=255)
    description: str = Field(default="", max_length=4000)
    amount_disputed: str | None = None


class DisputeResolve(BaseModel):
    status: str = Field(pattern="^(resolved_accepted|resolved_denied|withdrawn)$")
    resolution_notes: str = Field(min_length=4, max_length=4000)


@router.get("/disputes")
async def list_disputes(session: SessionDep, principal: Principal,
                        page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    if not principal.can("dispute.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    stmt = select(Dispute).where(Dispute.org_path.like(root + "%"))
    total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await session.execute(
        stmt.order_by(Dispute.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return {"items": [{
        "id": str(d.id), "dispute_number": d.dispute_number, "invoice_id": str(d.invoice_id),
        "customer_id": str(d.customer_id), "subject": d.subject, "status": d.status,
        "amount_disputed": str(d.amount_disputed) if d.amount_disputed is not None else None,
        "created_at": d.created_at.isoformat(),
        "resolution_notes": d.resolution_notes,
    } for d in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/disputes", status_code=201, dependencies=[CSRF])
async def create_dispute(body: DisputeCreate, session: SessionDep, principal: Principal):
    if not principal.can("dispute.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    inv = await session.get(Invoice, body.invoice_id)
    if inv is None or inv.deleted_at is not None:
        raise HTTPException(404, detail={"code": "not_found"})
    if not principal.is_platform_admin and not inv.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    # customers may only dispute their own invoices
    if principal.org_kind == "customer":
        cust = (await session.execute(
            select(Customer).where(Customer.org_path == principal.org_path).limit(1)
        )).scalar_one_or_none()
        if cust is None or inv.customer_id != cust.id:
            raise HTTPException(404, detail={"code": "not_found"})
    if inv.status not in ("issued", "exported", "paid_or_settled"):
        raise HTTPException(409, detail={"code": "not_disputable",
                                         "message": f"status {inv.status}"})
    await set_org_scope(session, inv.org_path)
    n = (await session.execute(
        select(func.count(Dispute.id)).where(Dispute.org_path == inv.org_path)
    )).scalar_one()
    d = Dispute(
        dispute_number=f"DSP-{inv.period_start:%Y%m}-{int(n) + 1:04d}",
        invoice_id=inv.id, customer_id=inv.customer_id,
        submitted_by=principal.user_id, org_id=inv.org_id, org_path=inv.org_path,
        subject=body.subject, description=body.description,
        amount_disputed=Decimal(body.amount_disputed) if body.amount_disputed else None,
    )
    session.add(d)
    # flag the invoice as disputed (issued→disputed transition keeps history)
    if inv.status in ("issued", "exported", "paid_or_settled"):
        inv.status = "disputed"
    await record_audit(session, principal, action="dispute.created", org_path=inv.org_path,
                       summary=f"Dispute '{body.subject}' filed for invoice {inv.invoice_number}",
                       entity_type="dispute", entity_id=d.id)
    await session.commit()
    return {"id": str(d.id), "dispute_number": d.dispute_number, "status": d.status}


@router.post("/disputes/{dispute_id}/resolve", dependencies=[CSRF])
async def resolve_dispute(dispute_id: uuid.UUID, body: DisputeResolve,
                          session: SessionDep, principal: Principal):
    if not principal.can("dispute.resolve"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    d = await session.get(Dispute, dispute_id)
    if d is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not d.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    await set_org_scope(session, d.org_path)
    d.status = body.status
    d.resolution_notes = body.resolution_notes
    d.resolved_at = datetime.now(UTC)
    d.resolved_by = principal.user_id
    inv = await session.get(Invoice, d.invoice_id)
    if inv is not None and inv.status == "disputed":
        inv.status = "under_review" if body.status == "resolved_accepted" else "issued"
        await record_audit(session, principal, action="invoice.state_changed",
                           org_path=inv.org_path,
                           summary=f"Invoice {inv.invoice_number}: disputed → {inv.status} "
                                   f"({body.status})",
                           entity_type="invoice", entity_id=inv.id)
    await record_audit(session, principal, action="dispute.resolved", org_path=d.org_path,
                       summary=f"Dispute {d.dispute_number} {body.status}",
                       entity_type="dispute", entity_id=d.id,
                       detail={"notes": body.resolution_notes[:500]})
    await session.commit()
    return {"ok": True, "status": d.status}

# ------------------------------------------------------- cloud accounts

@router.get("/customers/{customer_id}/account-families/{family_id}/accounts")
async def family_accounts(customer_id: uuid.UUID, family_id: uuid.UUID,
                          session: SessionDep, principal: Principal):
    """Cloud accounts mapped into an account family (allocation state visible)."""
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    fam = await session.get(AccountFamily, family_id)
    if fam is None or fam.customer_id != customer_id:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not fam.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    rows = (await session.execute(
        select(CloudAccount).where(CloudAccount.account_family_id == fam.id,
                                   CloudAccount.deleted_at.is_(None))
        .order_by(CloudAccount.external_id))).scalars().all()
    return [{"id": str(a.id), "provider": a.provider_code, "external_id": a.external_id,
             "name": a.display_name, "allocation_status": a.allocation_status,
             "account_kind": a.account_kind} for a in rows]


@router.get("/cloud-accounts")
async def list_cloud_accounts(session: SessionDep, principal: Principal,
                              allocation_status: str | None = Query(
                                  None, pattern="^(mapped|unmapped|excluded)$"),
                              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    """All cloud accounts in the caller's scope, newest first, with customer
    attribution and spend — the allocation worklist (unmapped filter)."""
    if not principal.can("cost.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    conds: list = [CloudAccount.org_path.like(root + "%"), CloudAccount.deleted_at.is_(None)]
    if allocation_status:
        conds.append(CloudAccount.allocation_status == allocation_status)
    total = int((await session.execute(
        select(func.count(CloudAccount.id)).where(*conds))).scalar_one())
    rows = (await session.execute(
        select(CloudAccount, AccountFamily.name, Customer.code, Customer.display_name)
        .outerjoin(AccountFamily, AccountFamily.id == CloudAccount.account_family_id)
        .outerjoin(Customer, Customer.id == AccountFamily.customer_id)
        .where(*conds)
        .order_by(CloudAccount.created_at.desc(), CloudAccount.id)
        .offset((page - 1) * page_size).limit(page_size))).all()
    # spend per account this-period-lifetime, one grouped query for the page
    ids = [a.id for a, *_ in rows]
    spend = {}
    if ids:
        srows = (await session.execute(
            select(CanonicalCostRecord.cloud_account_id,
                   func.sum(CanonicalCostRecord.provider_billed))
            .where(CanonicalCostRecord.cloud_account_id.in_(ids),
                   CanonicalCostRecord.line_item_type == "usage")
            .group_by(CanonicalCostRecord.cloud_account_id))).all()
        spend = {str(k): str(v or 0) for k, v in srows}
    return {"items": [{
        "id": str(a.id), "provider": a.provider_code, "external_id": a.external_id,
        "name": a.display_name, "allocation_status": a.allocation_status,
        "account_kind": a.account_kind, "family_name": fam_name,
        "customer_code": cust_code, "customer_name": cust_name,
        "lifetime_usage_cost": spend.get(str(a.id), "0"),
    } for a, fam_name, cust_code, cust_name in rows],
        "total": total, "page": page, "page_size": page_size}

class AccountMapRequest(BaseModel):
    account_family_id: uuid.UUID | None = None
    exclude: bool = False


@router.patch("/cloud-accounts/{account_id}", dependencies=[CSRF])
async def map_cloud_account(account_id: uuid.UUID, body: AccountMapRequest,
                            session: SessionDep, principal: Principal):
    """Assign an account to a customer account family (or exclude it from
    billing). Allocation decisions are audited; ingestion re-maps costs on
    the next run."""
    if not principal.can("customer.write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    acct = await session.get(CloudAccount, account_id)
    if acct is None or (not principal.is_platform_admin and not acct.org_path.startswith(root)):
        raise HTTPException(404, detail={"code": "not_found"})
    if body.exclude:
        acct.account_family_id = None
        acct.allocation_status = "excluded"
        await session.execute(
            update(CanonicalCostRecord)
            .where(CanonicalCostRecord.cloud_account_id == acct.id)
            .values(customer_id=None, account_family_id=None,
                    org_path=acct.org_path)
        )
        summary = f"Account {acct.external_id} excluded from billing"
    else:
        if acct.provider_code == "aws" and acct.account_kind == "payer":
            raise HTTPException(400, detail={
                "code": "payer_not_billee",
                "message": "a payer account consolidates linked accounts; map the linked accounts instead",
            })
        fam = await session.get(AccountFamily, body.account_family_id) if body.account_family_id else None
        if fam is None or not fam.org_path.startswith(root):
            raise HTTPException(400, detail={"code": "family_required"})
        acct.account_family_id = fam.id
        acct.allocation_status = "mapped"
        acct.mapped_at = datetime.now(UTC)
        # re-attribute already-ingested canonical rows to the new family/customer
        # (within caller scope) so pricing picks them up without re-ingesting
        await session.execute(
            update(CanonicalCostRecord)
            .where(CanonicalCostRecord.cloud_account_id == acct.id)
            .values(customer_id=fam.customer_id, account_family_id=fam.id,
                    org_path=fam.org_path)
        )
        summary = f"Account {acct.external_id} mapped to family '{fam.name}'"
    await record_audit(session, principal, action="cloud_account.mapped",
                       org_path=acct.org_path, summary=summary,
                       entity_type="cloud_account", entity_id=acct.id,
                       detail={"allocation_status": acct.allocation_status})
    await session.commit()
    return {"ok": True, "allocation_status": acct.allocation_status}

@router.get("/account-families")
async def list_all_families(session: SessionDep, principal: Principal):
    """Family picker source for account mapping (partner scope, light payload)."""
    if not principal.can("customer.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    rows = (await session.execute(
        select(AccountFamily, Customer.code, Customer.display_name)
        .join(Customer, Customer.id == AccountFamily.customer_id)
        .where(AccountFamily.org_path.like(root + "%"),
               AccountFamily.deleted_at.is_(None),
               Customer.deleted_at.is_(None))
        .order_by(Customer.display_name, AccountFamily.name))).all()
    return [{"id": str(f.id), "customer_id": str(f.customer_id), "name": f.name,
             "customer_code": code, "customer_name": name}
            for f, code, name in rows]
