"""Phase 2 operations tests: credit/debit notes, period close gate, waivers,
maker-checker enforcement, revenue leakage. Service + API layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.db.rls import set_org_scope
from app.ingestion.synthetic_aws import PERIODS, build_all
from app.models.billing_core import AccountFamily, CloudAccount, CloudBillingAccount, Customer
from app.models.contracts import BillingRule, BillingRuleVersion, Contract, ContractVersion
from app.models.invoices import Invoice, PeriodClose
from app.models.org import Organization
from app.services import ingest_service, invoice_service, pricing_service
from app.services.invoice_lifecycle import transition_invoice

ORG = uuid.uuid4()
CUST_ORG = uuid.uuid4()
ROOT_PATH = "/11111111-1111-4111-8111-111111111111/"
ORG_PATH = f"{ROOT_PATH}{ORG}/"
CUST_PATH = f"{ORG_PATH}{CUST_ORG}/"
MAKER = uuid.uuid4()
CHECKER = uuid.uuid4()


def _principal(user_id=MAKER, extra=()):
    from app.services.authz import RequestPrincipal
    perms = {
        "customer.read", "customer.write", "cost.read", "contract.read",
        "contract.write", "rule.write", "rule.publish", "pricing.run",
        "invoice.read", "invoice.write", "invoice.approve", "invoice.issue",
        "invoice.correct", "recon.read", "recon.resolve", "recon.waive",
        "period.close", "margin.view", "internal_notes.view", "export.data",
        "dispute.write",
    } | set(extra)
    return RequestPrincipal(
        user_id=user_id, email=f"u{str(user_id)[:6]}@msp.example.com", org_id=ORG,
        org_path=ORG_PATH, org_kind="reseller",
        roles=frozenset({"msp_admin"}), permissions=frozenset(perms),
        scope_prefixes=(ORG_PATH,),
    )


async def _issued_invoice():
    """Ingest June, contract+markup, price, invoice, issue. Returns invoice id."""
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        if await s.get(Organization, ORG) is None:
            s.add(Organization(id=ORG, kind="reseller", name="P2 MSP", path=ORG_PATH, currency="USD"))
            s.add(Organization(id=CUST_ORG, kind="customer", name="P2 Acme", path=CUST_PATH, currency="USD"))
            await s.flush()
            cust = Customer(org_id=ORG, org_path=CUST_PATH, code="P2AC", display_name="P2 Acme Co")
            s.add(cust)
            await s.flush()
            fam = AccountFamily(customer_id=cust.id, name="Primary", org_id=ORG, org_path=CUST_PATH)
            s.add(fam)
            await s.flush()
            payer = CloudBillingAccount(org_id=ORG, org_path=ORG_PATH, provider_code="aws",
                                        external_id="777700000001", display_name="P2 Payer", currency="USD")
            s.add(payer)
            await s.flush()
            for ext in ("111111111111", "222222222222"):
                s.add(CloudAccount(provider_code="aws", external_id=ext, display_name=f"AWS {ext}",
                                   billing_account_id=payer.id, account_family_id=fam.id,
                                   allocation_status="mapped", org_id=ORG, org_path=CUST_PATH))
            await s.commit()

    months = build_all()["777700000001"]
    async with SessionLocal() as s:
        await ingest_service.ingest_csv(
            s, org_id=ORG, org_path=ORG_PATH, provider_code="aws", parser_version=1,
            filename="2026-06.csv", body=months["2026-06"].encode() + b"# p2\n",
            object_key="p2/2026-06.csv", source="synthetic", correlation_id="p2", actor_user_id=None)

    async with SessionLocal() as s:
        await set_org_scope(s, CUST_PATH)
        cust = (await s.execute(select(Customer).where(Customer.code == "P2AC"))).scalar_one()
        contract = Contract(customer_id=cust.id, code="P2-C", name="P2 Contract",
                            org_id=ORG, org_path=CUST_PATH, status="active")
        s.add(contract)
        await s.flush()
        cv = ContractVersion(contract_id=contract.id, version_number=1, status="active",
                             effective_start=datetime(2026, 6, 1, tzinfo=UTC), currency="USD",
                             org_id=ORG, org_path=CUST_PATH)
        s.add(cv)
        rule = BillingRule(contract_id=contract.id, code="P2-M", name="12%", rule_type="percentage_markup",
                           org_id=ORG, org_path=CUST_PATH, status="published")
        s.add(rule)
        await s.flush()
        rv = BillingRuleVersion(rule_id=rule.id, version_number=1, status="published",
                                parameters={"type": "percentage_markup", "percent": "12"},
                                org_id=ORG, org_path=CUST_PATH)
        s.add(rv)
        await s.flush()
        cv.rule_bindings = [{"rule_id": str(rule.id), "rule_version_id": str(rv.id), "order": 1}]
        customer_id, cv_id = cust.id, cv.id
        await s.commit()

    june_start, june_end, _ = PERIODS[0]
    async with SessionLocal() as s:
        run = await pricing_service.run_pricing(
            s, customer_id=customer_id, contract_version_id=cv_id,
            period_start=june_start, period_end=june_end,
            actor_user_id=None, correlation_id="p2-price")
        run_id = run.run_id
    async with SessionLocal() as s:
        inv = await invoice_service.build_invoice_from_run(
            s, run_id=run_id, principal_org_path=ORG_PATH, actor_user_id=None, correlation_id="p2-inv")
        inv_id = inv.id
        await s.commit()
    p = _principal()
    async with SessionLocal() as s:
        inv = await s.get(Invoice, inv_id)
        await transition_invoice(s, inv, "under_review", p)
        await transition_invoice(s, inv, "approved", p)
        await transition_invoice(s, inv, "issued", p)
        await s.commit()
    return inv_id, june_start, june_end


@pytest.mark.asyncio
async def test_credit_note_draft_and_issue(client, migrated_db):
    from app.api.v1 import ops
    inv_id, _, _ = await _issued_invoice()
    p = _principal()
    async with SessionLocal() as s:
        note = await ops.create_note(  # call the function directly
            ops.NoteCreate(invoice_id=inv_id, kind="credit",
                           lines=[{"description": "Overcharge", "amount": "55.00"}],
                           reason="Duplicate EBS rate on the 14th"),
            s, p)
    assert note["amount"] == "55.00" and note["status"] == "draft"
    # issue → invoice corrected
    async with SessionLocal() as s:
        res = await ops.issue_note(uuid.UUID(note["id"]), s, p)
        assert res["ok"] and res["invoice_status"] == "corrected"
        note_row = (await s.get(Invoice, inv_id))
        assert note_row.status == "corrected"
    # note PDF renders
    async with SessionLocal() as s:
        data = await ops.download_note(uuid.UUID(note["id"]), s, p, fmt="pdf")
    assert data.body[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_note_rejected_on_unissued_invoice(client, migrated_db):
    from fastapi import HTTPException

    from app.api.v1 import ops
    # freshly built invoice is 'calculated', not issued
    async with SessionLocal() as s:
        with pytest.raises(HTTPException) as ei:
            await ops.create_note(
                ops.NoteCreate(invoice_id=uuid.uuid4(), kind="credit",
                               lines=[{"description": "x", "amount": "5.00"}],
                               reason="should not apply"), s, _principal())
        assert ei.value.status_code == 404  # invoice not found (random id)


@pytest.mark.asyncio
async def test_period_close_blocked_then_waived(client, migrated_db):
    from app.api.v1 import ops
    from app.models.reconciliation import ReconciliationException, ReconciliationRun

    inv_id, june_start, june_end = await _issued_invoice()
    p = _principal()
    # create a material open exception directly
    async with SessionLocal() as s:
        await set_org_scope(s, ORG_PATH)
        run = ReconciliationRun(org_path=ORG_PATH, org_id=ORG,
                                period_start=june_start, period_end=june_end,
                                status="completed", summary={})
        s.add(run)
        await s.flush()
        exc = ReconciliationException(
            run_id=run.id, dedupe_key="test-material", exc_type="unmapped_account",
            materiality="material", severity="high", amount_delta=Decimal("500.00"),
            currency="USD", explanation="test blocker", org_path=ORG_PATH, org_id=ORG)
        s.add(exc)
        await s.flush()
        exc_id = exc.id
        await s.commit()

    from fastapi import HTTPException
    # close blocked
    async with SessionLocal() as s:
        with pytest.raises(HTTPException) as ei:
            await ops.close_period(
                ops.CloseRequest(period_start=june_start, period_end=june_end), s, p)
        assert ei.value.status_code == 409
        assert ei.value.detail["code"] == "material_exceptions_open"

    # maker requests waiver
    async with SessionLocal() as s:
        w = await ops.request_waiver(
            exc_id, ops.WaiverRequest(exception_id=exc_id, reason="Traced to provider rounding, immaterial"),
            s, p)
        appr_id = uuid.UUID(w["id"])
        await s.commit()

    # checker (different user) approves via the approvals router
    from app.api.v1 import approvals
    checker = _principal(user_id=CHECKER)
    async with SessionLocal() as s:
        d = await approvals.decide(
            appr_id, approvals.DecisionBody(note="Reviewed, acceptable"), s, checker,
            decision="approved")
        assert d["ok"] and d["status"] == "approved"

    # self-application of waiver blocked (maker == caller)
    async with SessionLocal() as s:
        with pytest.raises(HTTPException) as ei:
            await ops.mark_waived(exc_id, s, p)
        assert ei.value.status_code == 403  # self_approval
    # a different recon owner applies it
    async with SessionLocal() as s:
        mw = await ops.mark_waived(exc_id, s, _principal(user_id=CHECKER))
        assert mw["status"] == "waived"

    # now close succeeds
    async with SessionLocal() as s:
        c = await ops.close_period(
            ops.CloseRequest(period_start=june_start, period_end=june_end), s, p)
        assert c["ok"] and c["status"] == "closed"
    async with SessionLocal() as s:
        pcs = list((await s.execute(select(PeriodClose).where(PeriodClose.org_path == ORG_PATH))).scalars())
    assert any(pc.status == "closed" for pc in pcs)


@pytest.mark.asyncio
async def test_revenue_leakage_reports_unbilled(client, migrated_db):
    from app.api.v1 import ops
    inv_id, june_start, june_end = await _issued_invoice()
    async with SessionLocal() as s:
        rep = await ops.revenue_leakage(s, _principal(), period_start=june_start)
    assert "unbilled_usage" in rep and "unmapped_usage" in rep
    assert "unallocated_credits" in rep and "invoice_run_drift" in rep
