"""API integration tests: auth flow, RBAC gates, tenant-scoped reads, seed
idempotency, audit trail. Uses the real FastAPI app over the migrated sqlite
database (RLS is exercised separately in tests_pg_isolation.py)."""

from __future__ import annotations

import pytest

import app.seed as seed_mod
from app.core.config import get_settings

DEMO_PW = None  # read from settings at runtime


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_seed_idempotent_and_complete(client, migrated_db):
    again = await seed_mod.seed()
    assert again.get("skipped") is True
    r = await client.get("/api/v1/auth/me")  # unauthenticated
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_health_live(client, migrated_db):
    r = await client.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client, migrated_db):
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "msp@northwind-msp.example.com", "password": "wrong-password-123"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_me_logout_flow(client, migrated_db):
    settings = get_settings()
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "msp@northwind-msp.example.com", "password": settings.seed_demo_password},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["user"]["org_kind"] == "reseller"
    assert "msp_admin" in data["user"]["roles"]
    assert "margin.view" in data["user"]["permissions"]
    csrf = data["csrf_token"]

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "msp@northwind-msp.example.com"

    # unsafe route without CSRF → 403
    bad = await client.post(
        "/api/v1/orgs",
        json={"kind": "reseller", "name": "No CSRF LLC", "parent_id": str(data["user"]["org_id"])},
    )
    assert bad.status_code == 403
    assert bad.json()["detail"]["code"] == "csrf_failed"

    out = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 200
    me2 = await client.get("/api/v1/auth/me")
    assert me2.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_denied_everywhere(client, migrated_db):
    for path in ("/api/v1/orgs", "/api/v1/users", "/api/v1/audit-events", "/api/v1/branding/current"):
        r = await client.get(path)
        assert r.status_code == 401, path


async def _login(client, email):
    settings = get_settings()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": settings.seed_demo_password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


@pytest.mark.asyncio
async def test_org_tree_scoped_to_subtree(client, migrated_db):
    await _login(client, "msp@northwind-msp.example.com")
    r = await client.get("/api/v1/orgs")
    assert r.status_code == 200
    orgs = r.json()
    # reseller admin sees: platform? no — must NOT see platform or the distributor
    # (their subtree starts at their own org; ancestors are not returned)
    kinds = {o["kind"] for o in orgs}
    assert "platform" not in kinds and "distributor" not in kinds
    assert {o["name"] for o in orgs} >= {"Northwind MSP", "Acme Cloud Co"}
    assert "Cascade IT Partners" not in {o["name"] for o in orgs}


@pytest.mark.asyncio
async def test_distributor_sees_only_own_branch(client, migrated_db):
    await _login(client, "dist@northwind-distribution.example.com")
    r = await client.get("/api/v1/orgs")
    names = {o["name"] for o in r.json()}
    assert "Northwind Distribution" in names and "Cascade IT Partners" in names
    assert "Southbridge Partners" not in names


@pytest.mark.asyncio
async def test_platform_admin_sees_all(client, migrated_db):
    await _login(client, "admin@cloudpartnerops.example.com")
    r = await client.get("/api/v1/orgs")
    names = {o["name"] for o in r.json()}
    assert {"Cloud PartnerOps Platform", "Northwind Distribution", "Southbridge Partners", "Quartz Analytics"} <= names


@pytest.mark.asyncio
async def test_cross_tenant_org_creation_denied(client, migrated_db):
    """A Cascade admin cannot create children under the Northwind MSP subtree."""
    await _login(client, "msp@cascade-it.example.com")
    northwind_msp_id = "33333333-3333-4333-8333-333333333333"
    r = await client.post(
        "/api/v1/orgs",
        json={"kind": "reseller", "name": "Sneaky LLC", "parent_id": northwind_msp_id},
        headers={"X-CSRF-Token": await _login(client, "msp@cascade-it.example.com")},
    )
    assert r.status_code in (403, 404)  # deny without existence leakage


@pytest.mark.asyncio
async def test_customer_cannot_administrate(client, migrated_db):
    await _login(client, "admin@acme-cloud.example.com")
    r = await client.post(
        "/api/v1/users",
        json={"email": "x@y.example.com", "display_name": "XY", "org_id": "44444444-4444-4444-8444-444444444441", "roles": ["customer_readonly"], "password": "Str0ng-Passw0rd-99"},
        headers={"X-CSRF-Token": await _login(client, "admin@acme-cloud.example.com")},
    )
    assert r.status_code == 403  # customer_admin has no user.manage


@pytest.mark.asyncio
async def test_capabilities_navigation_flags(client, migrated_db):
    await _login(client, "auditor@cloudpartnerops.example.com")
    r = await client.get("/api/v1/capabilities")
    nav = {e["key"]: e["granted"] for e in r.json()["navigation"]}
    assert nav["audit"] is True and nav["invoices"] is True
    assert nav["administration"] is False


@pytest.mark.asyncio
async def test_audit_scoped_to_granted_orgs(client, migrated_db):
    # auditor (platform org) sees everything under the platform root…
    await _login(client, "auditor@cloudpartnerops.example.com")
    r = await client.get("/api/v1/audit-events", params={"action": "login.success", "page_size": 100})
    assert r.status_code == 200
    summaries = " ".join(e["summary"] for e in r.json()["items"])
    assert "auditor@cloudpartnerops.example.com" in summaries
    # …while an MSP admin sees only their own subtree's logins
    await _login(client, "msp@northwind-msp.example.com")
    r2 = await client.get("/api/v1/audit-events", params={"action": "login.success", "page_size": 100})
    own = " ".join(e["summary"] for e in r2.json()["items"])
    assert "msp@northwind-msp.example.com" in own
    assert "auditor@cloudpartnerops.example.com" not in own


@pytest.mark.asyncio
async def test_analyst_without_audit_permission_denied(client, migrated_db):
    await _login(client, "finops@northwind-msp.example.com")
    r = await client.get("/api/v1/audit-events")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_customer_readonly_blocked_from_audit(client, migrated_db):
    await _login(client, "viewer@acme-cloud.example.com")
    r = await client.get("/api/v1/audit-events")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_branding_resolution_nearest_wins(client, migrated_db):
    await _login(client, "msp@northwind-msp.example.com")
    r = await client.get("/api/v1/branding/current")
    assert r.status_code == 200
    assert r.json()["product_name"] == "Northwind CloudBill"

    await _login(client, "dist@northwind-distribution.example.com")
    r2 = await client.get("/api/v1/branding/current")
    assert r2.json()["product_name"] == "Cloud PartnerOps"  # falls back to platform


@pytest.mark.asyncio
async def test_public_branding_no_auth(client, migrated_db):
    r = await client.get("/api/v1/branding/public")
    assert r.status_code == 200
    assert "product_name" in r.json()
