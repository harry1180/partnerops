"""Phase 3: connector service + API tests (sqlite; RLS semantics in pg suite).

Covers: create/list/toggle, duplicate rejection, evidence-based run-now
(fixture ingest recorded on the connector row), honest no_fixture status
for unknown periods, fetch_available is ALWAYS false (no fake live pull),
RBAC (customer role forbidden), and the scheduled due-pass advancing cadence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.db.rls import set_org_scope
from app.ingestion.synthetic_azure import BILLING_PROFILE
from app.models.audit import AuditEvent
from app.models.connectors import ProviderConnector
from app.models.cost import RawBillingFile
from app.models.org import Organization
from app.services.authz import RequestPrincipal

ROOT_PATH = "/11111111-1111-4111-8111-111111111111/"


async def _fresh_principal(tag: str) -> RequestPrincipal:
    """Own reseller org so tests never collide with seeded connectors."""
    org = uuid.uuid4()
    org_path = f"{ROOT_PATH}{org}/"
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        s.add(Organization(id=org, kind="reseller", name=f"Conn {tag}", path=org_path))
        await s.commit()
    return RequestPrincipal(
        user_id=uuid.uuid4(), email=f"conn-{tag}@example.com",
        org_id=org, org_path=org_path, org_kind="reseller",
        roles=frozenset({"msp_admin"}),
        permissions=frozenset({"integration.manage", "customer.write", "cost.read"}),
        scope_prefixes=(org_path,),
    )


@pytest.mark.asyncio
async def test_create_list_toggle_and_duplicate(client, migrated_db):
    principal = await _fresh_principal("a")
    async with SessionLocal() as s:
        c = await svc_create(s, principal, "Test Azure Export")
    assert c.next_due_at is not None and c.next_due_at > datetime.now(UTC)
    cid = c.id

    from app.services import connectors as svc

    async with SessionLocal() as s:
        items = await svc.list_connectors(s, principal.scope_prefixes[0])
        mine = [i for i in items if i["id"] == str(cid)]
        assert mine and mine[0]["fetch_available"] is False
        assert mine[0]["provider"] == "azure"

    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await svc_create(s, principal, "dupe")

    async with SessionLocal() as s:
        t = await svc.toggle_connector(s, principal, cid, False)
        assert t.enabled is False
    async with SessionLocal() as s:
        t = await svc.toggle_connector(s, principal, cid, True)
        assert t.enabled is True


async def svc_create(s, principal, name):
    from app.services import connectors as svc

    return await svc.create_connector(
        s, principal, name=name, provider_code="azure",
        connector_kind="azure_cost_export", billing_account_ref=BILLING_PROFILE,
        cadence="monthly", day_of_month=5, hour_utc=7, config={})


@pytest.mark.asyncio
async def test_run_now_records_evidence(client, migrated_db):
    principal = await _fresh_principal("b")
    async with SessionLocal() as s:
        c = await svc_create(s, principal, "Evidence Run")
        cid = c.id
    from app.services import connectors as svc

    async with SessionLocal() as s:
        result = await svc.run_connector_now(s, principal, cid, "2026-06")
    assert result.status == "ingested"
    assert result.canonical > 20
    assert result.file_id is not None

    async with SessionLocal() as s:
        row = await s.get(ProviderConnector, cid)
        assert row.last_ingest_at is not None
        assert str(row.last_file_id) == str(result.file_id)
        audits = (await s.execute(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "integration.connector_ran",
                AuditEvent.entity_id == cid)
        )).scalar_one()
        files = (await s.execute(
            select(func.count(RawBillingFile.id)).where(RawBillingFile.id == result.file_id)
        )).scalar_one()
    assert audits == 1
    assert files == 1  # a real file row backs the claim — no phantom ingest

    # re-running the same period is a duplicate-file skip, not an error
    async with SessionLocal() as s:
        again = await svc.run_connector_now(s, principal, cid, "2026-06")
    assert again.status == "ingested"
    assert again.detail and again.detail["skipped_duplicate_file"]


@pytest.mark.asyncio
async def test_run_now_unknown_period_is_honest(client, migrated_db):
    principal = await _fresh_principal("c")
    async with SessionLocal() as s:
        c = await svc_create(s, principal, "No Fixture")
        cid = c.id
    from app.services import connectors as svc

    async with SessionLocal() as s:
        r = await svc.run_connector_now(s, principal, cid, "2030-01")
    assert r.status == "no_fixture"
    assert r.file_id is None
    assert r.detail and "2026-06" in r.detail["known_periods"]


@pytest.mark.asyncio
async def test_run_due_connectors_ingests_previous_month(client, migrated_db):
    principal = await _fresh_principal("d")
    async with SessionLocal() as s:
        c = await svc_create(s, principal, "Due Pass")
        cid = c.id
    # force it due before the fake clock
    async with SessionLocal() as s:
        row = await s.get(ProviderConnector, cid)
        row.next_due_at = datetime(2026, 9, 1, tzinfo=UTC)
        await s.commit()

    from app.services import connectors as svc

    # fake now = Sept 3 → ingests the AUGUST fixture (previous full month)
    async with SessionLocal() as s:
        results = await svc.run_due_connectors(s, now=datetime(2026, 9, 3, 7, tzinfo=UTC))
    ingested = [r for r in results if r.connector_id == cid and r.status == "ingested"]
    assert ingested, results
    assert ingested[0].canonical > 20

    # advanced past the fake now → not due again until next cadence point
    async with SessionLocal() as s:
        row = await s.get(ProviderConnector, cid)
        got = row.next_due_at.replace(tzinfo=UTC) if row.next_due_at.tzinfo is None \
            else row.next_due_at  # sqlite reads back naive-UTC
        assert got > datetime(2026, 9, 3, 7, tzinfo=UTC)
    async with SessionLocal() as s:
        results2 = await svc.run_due_connectors(s, now=datetime(2026, 9, 3, 7, 10, tzinfo=UTC))
    assert [r for r in results2 if r.connector_id == cid] == []


@pytest.mark.asyncio
async def test_connector_api_rbac_and_flow(client, migrated_db):
    from app.services import connectors as svc  # noqa: F401  (import smoke)

    settings = get_settings()
    # customer admin holds no integration.manage → 403
    r = await client.post("/api/v1/auth/login", json={
        "email": "admin@acme-cloud.example.com", "password": settings.seed_demo_password})
    csrf = r.json()["csrf_token"]
    r = await client.get("/api/v1/connectors", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403

    # MSP admin sees the seeded connectors (seed wiring proof)
    r = await client.post("/api/v1/auth/login", json={
        "email": "msp@northwind-msp.example.com", "password": settings.seed_demo_password})
    csrf = r.json()["csrf_token"]
    r = await client.get("/api/v1/connectors", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    items = r.json()["items"]
    names = {i["name"] for i in items}
    assert {"Northwind AWS CUR", "Northwind Azure Cost Export"} <= names, names
    assert all(i["fetch_available"] is False for i in items)
    az = [i for i in items if i["provider"] == "azure"][0]

    # run the seeded Azure connector for July → real ingest evidence
    r = await client.post(f"/api/v1/connectors/{az['id']}/run", json={"period": "2026-07"},
                          headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ingested"
    assert r.json()["canonical"] > 20

    # create with an unknown account: honest no_fixture, still 200
    body = {"name": f"Custom {uuid.uuid4().hex[:6]}", "provider_code": "azure",
            "billing_account_ref": "custom-acct-1", "cadence": "monthly"}
    r = await client.post("/api/v1/connectors", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = await client.post(f"/api/v1/connectors/{cid}/run", json={"period": "2026-06"},
                          headers={"X-CSRF-Token": csrf})
    assert r.json()["status"] == "no_fixture"

    # duplicate on the same (provider, account) → 409
    r = await client.post("/api/v1/connectors", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409

    # cross-tenant: Cascade admin cannot even see/run Northwind's connector
    r = await client.post("/api/v1/auth/login", json={
        "email": "msp@cascade-it.example.com", "password": settings.seed_demo_password})
    csrf2 = r.json()["csrf_token"]
    r = await client.get("/api/v1/connectors", headers={"X-CSRF-Token": csrf2})
    assert az["id"] not in {i["id"] for i in r.json()["items"]}
    r = await client.post(f"/api/v1/connectors/{az['id']}/run", json={"period": "2026-07"},
                          headers={"X-CSRF-Token": csrf2})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_azure_synthetic_load_endpoint(client, migrated_db):
    settings = get_settings()
    r = await client.post("/api/v1/auth/login", json={
        "email": "msp@northwind-msp.example.com", "password": settings.seed_demo_password})
    csrf = r.json()["csrf_token"]
    r = await client.post("/api/v1/ingestion/synthetic/azure/load",
                          data={"months": "2026-06"},
                          headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    res = r.json()["results"][0]
    assert res["canonical"] > 20
    assert any(a.startswith("unmapped-") for a in res["unmapped_accounts"])
    # idempotent re-load skips cleanly
    r = await client.post("/api/v1/ingestion/synthetic/azure/load",
                          data={"months": "2026-06"},
                          headers={"X-CSRF-Token": csrf})
    assert r.json()["results"][0]["skipped_duplicate_file"]
    # bad month → 400
    r = await client.post("/api/v1/ingestion/synthetic/azure/load",
                          data={"months": "2030-01"},
                          headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
