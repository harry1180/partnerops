"""Phase 4 API tests: finops + governance endpoints against the seeded demo.

Uses the real login flow (httpx in-process client) so permission gates, CSRF
and org scoping are exercised end-to-end, not just service calls.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import get_settings


async def _login(client, email: str) -> str:
    settings = get_settings()
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": settings.seed_demo_password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def _h(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


async def _ensure_demo_data(client, csrf: str) -> None:
    """Seed ships no cost rows; load synthetic AWS + Azure fixtures
    (idempotent). Recommendations need Azure's dev-vs-prod + orphan pattern."""
    r = await client.post("/api/v1/ingestion/synthetic/load",
                          data={"months": "2026-06,2026-07,2026-08"}, headers=_h(csrf))
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/v1/ingestion/synthetic/azure/load",
                          data={"months": "2026-06,2026-07,2026-08"}, headers=_h(csrf))
    assert r.status_code in (200, 201), r.text


@pytest.mark.asyncio
async def test_budgets_crud_and_status(client, migrated_db):
    csrf = await _login(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)
    suffix = uuid.uuid4().hex[:6]
    r = await client.get("/api/v1/customers?search=Cobalt", headers=_h(csrf))
    coba = next(x for x in r.json()["items"] if x["code"] == "COBA")

    # seeded demo budget present (Cobalt's August period is current-ish so
    # the active window shows it; the 14x anomaly spikes it past the cap)
    r = await client.get("/api/v1/budgets", headers=_h(csrf))
    assert r.status_code == 200
    seeded = [b for b in r.json()["items"] if b["name"] == "Cobalt August cap"]
    assert seeded and Decimal(seeded[0]["actual"]) > 0

    # create one
    body = {"name": f"API budget {suffix}", "amount": "2500.00",
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00",
            "customer_id": coba["id"], "alert_threshold_pct": 90}
    r = await client.post("/api/v1/budgets", json=body, headers=_h(csrf))
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    r = await client.get("/api/v1/budgets?include_closed=true", headers=_h(csrf))
    mine = [b for b in r.json()["items"] if b["id"] == bid]
    assert mine, "created budget missing"
    assert Decimal(mine[0]["actual"]) > 0
    assert mine[0]["over_budget"]  # Cobalt June spend > 2500 → breached

    r = await client.delete(f"/api/v1/budgets/{bid}", headers=_h(csrf))
    assert r.status_code == 200
    r = await client.get("/api/v1/budgets?include_closed=true", headers=_h(csrf))
    assert bid not in {b["id"] for b in r.json()["items"]}


@pytest.mark.asyncio
async def test_finops_passes_and_anomaly_review_api(client, migrated_db):
    csrf = await _login(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)

    # anomaly pass over demo data (Cobalt August spike is planted in fixtures)
    r = await client.post("/api/v1/finops/anomalies/run",
                          data={"threshold_z": "4.0", "abs_floor": "500"}, headers=_h(csrf))
    assert r.status_code == 200, r.text
    assert r.json()["created"] >= 1 or r.json()["updated"] >= 1  # rerunnable

    r = await client.get("/api/v1/finops/anomalies?status=open", headers=_h(csrf))
    items = r.json()["items"]
    assert items
    anom = items[0]
    assert anom["method"] == "mom_robust_zscore_median_mad"

    # review flow: acknowledge with note → audited
    r = await client.patch(f"/api/v1/finops/anomalies/{anom['id']}",
                           json={"status": "acknowledged", "note": "known event"},
                           headers=_h(csrf))
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    r = await client.get("/api/v1/audit-events?page=1&page_size=100", headers=_h(csrf))
    assert any(e["action"] == "anomaly.reviewed" for e in r.json()["items"])


@pytest.mark.asyncio
async def test_recommendation_flow_api(client, migrated_db):
    csrf = await _login(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)
    r = await client.post("/api/v1/finops/recommendations/run", headers=_h(csrf))
    assert r.status_code == 200, r.text

    r = await client.get("/api/v1/finops/recommendations?status=open", headers=_h(csrf))
    items = r.json()["items"]
    assert items, r.json()
    assert all(i["basis"] for i in items)
    rec = items[0]
    r = await client.post(f"/api/v1/finops/recommendations/{rec['id']}/decision",
                          json={"decision": "accept", "note": "team agreed"},
                          headers=_h(csrf))
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    # realized total must stay 0 — no post-decision data in the demo world
    r = await client.get("/api/v1/finops/recommendations", headers=_h(csrf))
    assert Decimal(r.json()["realized_total"]) == 0


@pytest.mark.asyncio
async def test_forecast_and_unit_economics_api(client, migrated_db):
    csrf = await _login(client, "msp@northwind-msp.example.com")
    # ensure demo data ingested (journey may already have; smoke-style idempotent)
    await client.post("/api/v1/ingestion/synthetic/load",
                      data={"months": "2026-06,2026-07,2026-08"}, headers=_h(csrf))
    r = await client.get("/api/v1/finops/forecast?months_ahead=3", headers=_h(csrf))
    assert r.status_code == 200
    body = r.json()
    assert body["method"] in ("avg_mom_growth_linear", "insufficient_history")
    if body["method"] == "avg_mom_growth_linear":
        assert len(body["forecast"]) == 3

    r = await client.get("/api/v1/finops/unit-economics", headers=_h(csrf))
    assert r.status_code == 200
    dims = {d["dimension"] for d in r.json()["dimensions"]}
    assert {"application", "environment", "owner", "cost_center"} <= dims

    r = await client.get("/api/v1/finops/tag-compliance", headers=_h(csrf))
    assert r.status_code == 200
    body = r.json()
    assert any(0 <= c["pct"] <= 100 for c in body["coverage"])


@pytest.mark.asyncio
async def test_governance_api_flow(client, migrated_db):
    csrf = await _login(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)

    # seeded policies visible
    r = await client.get("/api/v1/governance/policies", headers=_h(csrf))
    assert r.status_code == 200
    names = {p["name"] for p in r.json()["items"]}
    assert "Approved regions: eastus2 only" in names and \
           "Every account must map to a customer" in names

    # evaluate → findings
    r = await client.post("/api/v1/governance/evaluate", headers=_h(csrf))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["open"] >= 1
    assert "approved_regions" in body["by_policy"]

    r = await client.get("/api/v1/governance/findings?status=open", headers=_h(csrf))
    findings = r.json()["items"]
    assert findings
    f = findings[0]
    assert f["evidence"] and f["severity"]

    # exception grant
    r = await client.post(f"/api/v1/governance/findings/{f['id']}/exception",
                          json={"reason": "tracked GOV-42", "expires_at": "2026-12-31T00:00:00+00:00"},
                          headers=_h(csrf))
    assert r.status_code == 201, r.text
    r = await client.get("/api/v1/governance/findings?status=open", headers=_h(csrf))
    assert f["id"] not in {x["id"] for x in r.json()["items"]}  # now excepted

    # bad expiry rejected
    r = await client.post(f"/api/v1/governance/findings/{f['id']}/exception",
                          json={"reason": "past", "expires_at": "2020-01-01T00:00:00+00:00"},
                          headers=_h(csrf))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_phase4_rbac(client, migrated_db):
    # customer roles: budget.manage on own is allowed to read; but partner-only
    # governance/finops passes are forbidden for customers
    csrf = await _login(client, "admin@acme-cloud.example.com")
    r = await client.get("/api/v1/budgets", headers=_h(csrf))
    assert r.status_code == 400  # must-scope: org_kind=customer rejected explicitly
    r = await client.post("/api/v1/finops/anomalies/run", data={}, headers=_h(csrf))
    assert r.status_code == 403
    r = await client.get("/api/v1/finops/anomalies", headers=_h(csrf))
    assert r.status_code == 403
    r = await client.post("/api/v1/governance/evaluate", headers=_h(csrf))
    assert r.status_code == 403

    # finops analyst can run passes; billing analyst cannot
    csrf = await _login(client, "finops@northwind-msp.example.com")
    r = await client.get("/api/v1/finops/anomalies", headers=_h(csrf))
    assert r.status_code == 200
    r = await client.post("/api/v1/finops/recommendations/run", headers=_h(csrf))
    assert r.status_code == 200
    csrf = await _login(client, "billing@northwind-msp.example.com")
    r = await client.post("/api/v1/finops/anomalies/run", data={}, headers=_h(csrf))
    assert r.status_code == 403
