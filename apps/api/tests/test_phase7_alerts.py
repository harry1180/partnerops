"""Phase 7: budget alert episodes + orphan.discovered event.

Service-level: full episode state machine (open → stay-open → recover →
re-arm) on a synthetic workspace with real fixture spend, delivery rows
with payload correctness, role-routed emails. API-level: RBAC on the
alert pass, Cobalt demo budget opening its first episode, duplicate
suppression on re-run, outbox recipients, deliveries for subscribers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.db.rls import set_org_scope
from app.ingestion.synthetic_aws import build_all
from app.models.approvals import NotificationOutbox, WebhookDelivery, WebhookEndpoint
from app.models.auth import Role, User, UserRoleAssignment
from app.models.billing_core import AccountFamily, CloudAccount, CloudBillingAccount, Customer
from app.models.finops import Budget
from app.models.org import Organization
from app.services import alerts
from app.services import webhooks as wh
from app.services.ingest_service import ingest_csv

ROOT_PATH = "/11111111-1111-4111-8111-111111111111/"


def _h(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


async def _login_csrf(client, email: str) -> str:
    settings = get_settings()
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": settings.seed_demo_password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


async def _synth_workspace(tag: str, *, with_endpoint: bool = False):
    """Synthetic org + customer with mapped/partially-mapped accounts; AWS
    fixture ingested AFTER the optional webhook endpoint exists, so the
    orphan.discovered event from unmapped accounts lands as a delivery row.
    Returns (org_id, org_path, cust_id, endpoint_secret_or_None)."""
    org = uuid.uuid4()
    cust = uuid.uuid4()
    org_path = f"{ROOT_PATH}{org}/"
    cust_path = f"{org_path}{cust}/"
    secret = None
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        s.add(Organization(id=org, kind="reseller", name=f"P7 {tag}",
                           path=org_path, currency="USD"))
        s.add(Organization(id=cust, kind="customer", name=f"P7C {tag}",
                           path=cust_path, currency="USD"))
        await s.flush()
        c = Customer(org_id=org, org_path=cust_path, code=f"P7{tag[:5]}",
                     display_name=f"P7 Customer {tag}")
        s.add(c)
        await s.flush()
        fam = AccountFamily(customer_id=c.id, name="F", org_id=org, org_path=cust_path)
        s.add(fam)
        await s.flush()
        payer = CloudBillingAccount(org_id=org, org_path=org_path, provider_code="aws",
                                    external_id="777700000001", display_name="P", currency="USD")
        s.add(payer)
        await s.flush()
        # map ONLY 111 → every other linked account is unmapped → orphan event
        s.add(CloudAccount(provider_code="aws", external_id="111111111111",
                           display_name="mapped", billing_account_id=payer.id,
                           account_family_id=fam.id, allocation_status="mapped",
                           org_id=org, org_path=cust_path))
        await s.commit()
        if with_endpoint:
            _ep, issue = await wh.create_endpoint(
                s, org_path, url="http://127.0.0.1:1/hook",
                events=["orphan.discovered", "budget.over_threshold"],
                description="p7 test")
            secret = issue.secret
            await s.commit()
    aws = build_all()["777700000001"]
    async with SessionLocal() as s:
        for label in ("2026-06",):
            await ingest_csv(s, org_id=org, org_path=org_path, provider_code="aws",
                             parser_version=1, filename=f"{label}.csv",
                             body=aws[label].encode(), object_key=f"p7/{tag}/{label}.csv",
                             source="synthetic", correlation_id=None, actor_user_id=None)
    return org, org_path, str(c.id), secret


async def _budget(org, org_path, cust_id, *, amount="1000.00", name="cap"):
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        b = Budget(org_id=org, org_path=org_path, name=name,
                   scope_kind="customer", customer_id=uuid.UUID(cust_id),
                   provider_code="aws", amount=Decimal(amount), currency="USD",
                   period_start=datetime(2026, 6, 1, tzinfo=UTC),
                   period_end=datetime(2026, 7, 1, tzinfo=UTC),
                   alert_threshold_pct=80)
        s.add(b)
        await s.commit()
        return b.id


# ---------------- episode state machine ----------------

@pytest.mark.asyncio
async def test_episode_open_stay_recover_rearm(client, migrated_db):
    tag = uuid.uuid4().hex[:6]
    org, org_path, cust, secret = await _synth_workspace(tag, with_endpoint=True)
    bid = await _budget(org, org_path, cust, amount="1000.00", name=f"cap {tag}")
    now = datetime(2026, 7, 15, tzinfo=UTC)  # closed period → exact actuals

    async with SessionLocal() as s:
        r1 = await alerts.evaluate_budget_alerts(s, org_path, now=now)
        await s.commit()
        assert r1.evaluated == 1 and r1.opened == 1
        b = await s.get(Budget, bid)
        assert b.alert_state["breached"] is True and b.alert_state["alert_count"] == 1

        # still breached → re-run sends nothing
        deliveries = int((await s.execute(
            select(func.count(WebhookDelivery.id)).where(
                WebhookDelivery.org_path == org_path,
                WebhookDelivery.event_type == "budget.over_threshold")
        )).scalar_one())
        assert deliveries == 1
        r2 = await alerts.evaluate_budget_alerts(s, org_path, now=now)
        await s.commit()
        assert r2.opened == 0 and r2.still_open == 1
        n2 = int((await s.execute(
            select(func.count(WebhookDelivery.id)).where(
                WebhookDelivery.event_type == "budget.over_threshold",
                WebhookDelivery.org_path == org_path)
        )).scalar_one())
        assert n2 == 1, "no duplicate alerts within one episode"

    # recovery: big cap → closes
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        b = await s.get(Budget, bid)
        b.amount = Decimal("999999.00")
        await s.commit()
    async with SessionLocal() as s:
        r3 = await alerts.evaluate_budget_alerts(s, org_path, now=now)
        await s.commit()
        assert r3.closed == 1
        b = await s.get(Budget, bid)
        assert b.alert_state["breached"] is False and b.alert_state["recovered_at"]

    # re-arm: breach again → episode #2
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        b = await s.get(Budget, bid)
        b.amount = Decimal("1000.00")
        await s.commit()
    async with SessionLocal() as s:
        r4 = await alerts.evaluate_budget_alerts(s, org_path, now=now)
        await s.commit()
        assert r4.opened == 1
        b = await s.get(Budget, bid)
        assert b.alert_state["alert_count"] == 2
        ev = (await s.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.org_path == org_path,
                WebhookDelivery.event_type == "budget.over_threshold")
            .order_by(WebhookDelivery.created_at.desc()).limit(1)
        )).scalars().first()
        p = ev.payload
        assert p["budget_id"] == str(bid) and p["episode"] == 2
        assert Decimal(p["pct_of_budget"]) > 80 and p["over_budget"] is True


@pytest.mark.asyncio
async def test_orphan_event_delivered_on_ingest_with_unmapped(client, migrated_db):
    tag = uuid.uuid4().hex[:6]
    _org, org_path, _cust, secret = await _synth_workspace(tag, with_endpoint=True)
    async with SessionLocal() as s:
        await set_org_scope(s, "/")
        rows = list((await s.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.org_path == org_path,
                WebhookDelivery.event_type == "orphan.discovered")
        )).scalars())
        assert len(rows) == 1
        assert rows[0].status == "pending"
        accs = rows[0].payload["accounts"]
        assert "222222222222" in accs and "111111111111" not in accs
        assert secret is None or secret.startswith("whsec_")
        # sealed at rest
        ep = (await s.execute(select(WebhookEndpoint).where(
            WebhookEndpoint.org_path == org_path))).scalars().first()
        assert ep.secret_ref.startswith("enc:v1:")


@pytest.mark.asyncio
async def test_email_routing_is_role_scoped(client, migrated_db):
    tag = uuid.uuid4().hex[:6]
    org, org_path, cust, _ = await _synth_workspace(tag)
    await _budget(org, org_path, cust, amount="1000.00", name=f"routed {tag}")
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        pw = (await s.execute(select(User.password_hash).limit(1))).scalar_one()
        fin_role = (await s.execute(select(Role).where(Role.key == "finops_analyst"))).scalar_one()
        bill_role = (await s.execute(select(Role).where(Role.key == "billing_analyst"))).scalar_one()
        u1 = User(email=f"fin-{tag}@p7.example.com", display_name="F",
                  password_hash=pw, home_org_id=org, status="active")
        u2 = User(email=f"bill-{tag}@p7.example.com", display_name="B",
                  password_hash=pw, home_org_id=org, status="active")
        u3 = User(email=f"far-{tag}@p7.example.com", display_name="X",
                  password_hash=pw, home_org_id=uuid.uuid4(), status="active")  # no org row → other subtree
        s.add_all([u1, u2, u3])
        await s.flush()
        s.add(UserRoleAssignment(user_id=u1.id, role_id=fin_role.id, org_id=org))
        s.add(UserRoleAssignment(user_id=u2.id, role_id=bill_role.id, org_id=org))
        await s.commit()
        await alerts.evaluate_budget_alerts(s, org_path,
                                            now=datetime(2026, 7, 15, tzinfo=UTC))
        await s.commit()
        rows = list((await s.execute(
            select(NotificationOutbox.recipient).where(
                NotificationOutbox.org_path == org_path,
                NotificationOutbox.kind == "budget_alert")
        )).all())
        recipients = sorted(r[0] for r in rows)
        assert recipients == [f"fin-{tag}@p7.example.com"]  # not billing, not far


# ---------------- API flow over the demo workspace ----------------

@pytest.mark.asyncio
async def test_alert_api_flow(client, migrated_db):
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    r = await client.post("/api/v1/ingestion/synthetic/load",
                          data={"months": "2026-06,2026-07,2026-08"}, headers=_h(csrf))
    assert r.status_code in (200, 201), r.text

    # endpoint subscribed to the alert event
    r = await client.post("/api/v1/integrations/webhooks", json={
        "url": "http://127.0.0.1:1/hook",
        "events": ["budget.over_threshold"]}, headers=_h(csrf))
    assert r.status_code == 201

    # first alert pass: the seeded Cobalt cap is over threshold (14x fixture)
    r = await client.post("/api/v1/budgets/evaluate-alerts", json={}, headers=_h(csrf))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["evaluated"] >= 1 and res["opened"] >= 1

    # re-run: episode stays open, zero new sends
    r = await client.post("/api/v1/budgets/evaluate-alerts", json={}, headers=_h(csrf))
    res2 = r.json()
    assert res2["opened"] == 0 and res2["still_open"] >= 1

    # budgets feed now carries episode state
    r = await client.get("/api/v1/budgets", headers=_h(csrf))
    cob = next(b for b in r.json()["items"] if b["name"] == "Cobalt August cap")
    assert cob["alert_state"] and cob["alert_state"]["breached"] is True

    # emails: partner alert roles in the MSP subtree AND the budget's own
    # customer admins (customer-scoped budget), never billing_analyst
    async with SessionLocal() as s:
        await set_org_scope(s, "/")
        recips = {row[0] for row in (await s.execute(
            select(NotificationOutbox.recipient).where(
                NotificationOutbox.kind == "budget_alert",
                NotificationOutbox.subject.like("%Cobalt August cap%")))).all()}
        # partner alert roles get it; billing_analyst (no alert role) does not;
        # Cobalt has no customer_admin user, so no customer emails either
        assert "msp@northwind-msp.example.com" in recips
        assert "finops@northwind-msp.example.com" in recips
        assert "billing@northwind-msp.example.com" not in recips

    # webhook subscriber got a pending delivery; sweep attempts it
    r = await client.get("/api/v1/integrations/overview", headers=_h(csrf))
    eid = next(e["id"] for e in r.json()["webhook_endpoints"]
               if "budget.over_threshold" in e["events"])
    r = await client.get(f"/api/v1/integrations/webhooks/{eid}/deliveries", headers=_h(csrf))
    assert any(d["event"] == "budget.over_threshold" for d in r.json()["items"])
    r = await client.post(f"/api/v1/integrations/webhooks/deliver?endpoint_id={eid}",
                          headers=_h(csrf))
    assert r.status_code == 200 and r.json()["attempted"] >= 1

    # audited
    r = await client.get("/api/v1/audit-events?page_size=100", headers=_h(csrf))
    assert any(e["action"] == "budget.alerts_evaluated" for e in r.json()["items"])


@pytest.mark.asyncio
async def test_alert_api_rbac(client, migrated_db):
    # viewer (no alert.manage) refused
    csrf_v = await _login_csrf(client, "viewer@acme-cloud.example.com")
    r = await client.post("/api/v1/budgets/evaluate-alerts", json={}, headers=_h(csrf_v))
    assert r.status_code == 403
    # acme customer_admin also lacks alert.manage → same gate (the partner-root
    # guard would reject them next, but permission check fires first)
    csrf_a = await _login_csrf(client, "admin@acme-cloud.example.com")
    r = await client.post("/api/v1/budgets/evaluate-alerts", json={}, headers=_h(csrf_a))
    assert r.status_code == 403
