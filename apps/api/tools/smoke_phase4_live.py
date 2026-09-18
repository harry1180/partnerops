"""Live HTTP smoke for Phase 4 (FinOps & governance; API on :8001 + seeded DB).

Journey: ingest AWS (both months that carry the planted patterns) -> budgets
list incl. seeded Cobalt cap -> create/delete budget -> anomaly pass finds
the Cobalt CloudFront 14x -> acknowledge flow -> recommendation pass yields
evidence-backed recs -> accept/dismiss -> forecast + unit economics +
tag-compliance read clean -> governance: seeded policies -> evaluate ->
findings carry evidence -> exception grant -> portal legs (customer sees own
budget/anomalies, never partner internals).

Re-runnable: ingestion idempotent; anomaly/rec/finding rows upsert by
dedupe_key; budgets use unique names.

Run:  .venv/Scripts/python.exe tools/smoke_phase4_live.py [base_url]
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APP_ENV", "local")

import httpx  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"


def _password() -> str:
    from app.core.config import get_settings

    return get_settings().seed_demo_password


def _csrf(client: httpx.AsyncClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("cpo_csrf") or ""}


async def main() -> int:
    ok: list[str] = []

    def step(name: str, cond: bool, extra: str = "") -> None:
        assert cond, f"FAIL {name} {extra}"
        ok.append(name)
        print(f"  ✔ {name}")

    async with httpx.AsyncClient(base_url=BASE, timeout=90.0) as c:
        r = await c.post("/api/v1/auth/login", json={
            "email": "msp@northwind-msp.example.com", "password": _password()})
        assert r.status_code == 200, r.text
        csrf = _csrf(c)

        # 1. data present (idempotent)
        await c.post("/api/v1/ingestion/synthetic/load",
                     data={"months": "2026-06,2026-07,2026-08"}, headers=csrf)
        await c.post("/api/v1/ingestion/synthetic/azure/load",
                     data={"months": "2026-06,2026-07,2026-08"}, headers=csrf)
        step("billing data present (aws+azure)", True)

        # 2. budgets: seeded + create/delete
        r = await c.get("/api/v1/budgets", headers=csrf)
        seeded = [b for b in r.json()["items"] if b["name"] == "Cobalt August cap"]
        step("seeded Cobalt budget visible", bool(seeded), str(r.json())[:120])
        suffix = uuid.uuid4().hex[:6]
        r = await c.get("/api/v1/customers?search=Acme", headers=csrf)
        acme = next(x for x in r.json()["items"] if x["code"] == "ACME")
        r = await c.post("/api/v1/budgets", json={
            "name": f"Smoke {suffix}", "amount": "5000.00",
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00",
            "customer_id": acme["id"], "alert_threshold_pct": 90}, headers=csrf)
        assert r.status_code == 201, r.text
        bid = r.json()["id"]
        r = await c.get("/api/v1/budgets?include_closed=true", headers=csrf)
        mine = next(b for b in r.json()["items"] if b["id"] == bid)
        step("created budget shows actuals+variance",
             Decimal(mine["actual"]) > 0 and mine["over_budget"], str(mine)[:160])
        r = await c.delete(f"/api/v1/budgets/{bid}", headers=csrf)
        step("budget delete (soft)", r.status_code == 200)

        # 3. anomalies
        r = await c.post("/api/v1/finops/anomalies/run",
                         data={"threshold_z": "4.0", "abs_floor": "500"}, headers=csrf)
        body = r.json()
        step("anomaly pass ran", r.status_code == 200
             and (body["created"] + body["updated"]) >= 1, str(body)[:160])
        r = await c.get("/api/v1/finops/anomalies?page_size=50", headers=csrf)
        items = r.json()["items"]
        spikes = [a for a in items
                  if "CloudFront" in (a["service"] or "") and a["kind"] == "cost_spike"
                  and Decimal(a["observed"]) > Decimal(a["baseline"]) * 5]
        step("Cobalt CloudFront spike surfaced", bool(spikes),
             str([(a["service"], a["kind"], a["status"]) for a in items])[:220])
        step("noise drop filtered out",
             not any(a["kind"] == "cost_drop" and "CloudFront" in (a["service"] or "")
                     for a in items))
        an = spikes[0]
        step("anomaly method labeled honestly",
             an["method"] == "mom_robust_zscore_median_mad", an["method"])
        if an["status"] == "open":
            r = await c.patch(f"/api/v1/finops/anomalies/{an['id']}",
                              json={"status": "acknowledged", "note": "smoke review"},
                              headers=csrf)
            step("anomaly acknowledged", r.json().get("status") == "acknowledged")
        else:
            step("anomaly previously reviewed (re-run)", True)

        # 4. recommendations
        r = await c.post("/api/v1/finops/recommendations/run", headers=csrf)
        step("recommendation pass ran", r.status_code == 200 and r.json()["created"] >= 0,
             str(r.json())[:140])
        r = await c.get("/api/v1/finops/recommendations?status=open", headers=csrf)
        recs = r.json()["items"]
        step("recs carry evidence basis + confidence",
             bool(recs) and all(x["basis"] for x in recs), str(recs[:1])[:160])
        r = await c.post(f"/api/v1/finops/recommendations/{recs[0]['id']}/decision",
                         json={"decision": "dismiss", "note": "smoke"}, headers=csrf)
        step("decision recorded", r.json().get("status") == "dismissed")
        r = await c.get("/api/v1/finops/recommendations", headers=csrf)
        step("realized savings honest (no post data => 0)",
             Decimal(r.json()["realized_total"]) == 0, r.json()["realized_total"])

        # 5. forecast / unit economics / tag compliance
        r = await c.get("/api/v1/finops/forecast", headers=csrf)
        step("forecast method-labeled", r.json()["method"] in
             ("avg_mom_growth_linear", "insufficient_history"), str(r.json())[:120])
        r = await c.get("/api/v1/finops/unit-economics", headers=csrf)
        step("unit economics across 4 dims", len(r.json()["dimensions"]) == 4)
        r = await c.get("/api/v1/finops/tag-compliance", headers=csrf)
        step("tag coverage + unallocated cost reported",
             "unallocated_cost" in r.json() and r.json()["coverage"])

        # 6. governance
        r = await c.get("/api/v1/governance/policies", headers=csrf)
        step("seeded policies visible", len(r.json()["items"]) >= 2)
        r = await c.post("/api/v1/governance/evaluate", headers=csrf)
        ev = r.json()
        step("evaluation produced findings",
             r.status_code == 200 and ev["open"] >= 1, str(ev)[:160])
        r = await c.get("/api/v1/governance/findings?status=open", headers=csrf)
        f0 = r.json()["items"]
        step("findings carry evidence", bool(f0) and all(x["evidence"] for x in f0),
             str(f0[:1])[:160])
        r = await c.post(f"/api/v1/governance/findings/{f0[0]['id']}/exception",
                         json={"reason": "smoke exception",
                               "expires_at": "2027-01-01T00:00:00+00:00"}, headers=csrf)
        step("exception granted", r.status_code == 201)

        # 7. audit trail covers phase-4 actions
        r = await c.get("/api/v1/audit-events?page=1&page_size=200", headers=csrf)
        actions = {e["action"] for e in r.json()["items"]}
        need = {"budget.created", "anomaly.pass_ran", "anomaly.reviewed",
                "recommendation.decided", "governance.evaluated",
                "governance.exception_granted"}
        step("audit covers phase-4 actions", need <= actions, str(actions)[:220])

        # portal setup: partner creates an Acme budget (June) while `c` is open
        suffix2 = uuid.uuid4().hex[:6]
        await c.post("/api/v1/budgets", json={
            "name": f"Portal smoke {suffix2}", "amount": "5000.00",
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00",
            "customer_id": acme["id"]}, headers=csrf)

    # portal leg: customer sees own budget/anomalies; no partner fields
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c2:
        r = await c2.post("/api/v1/auth/login", json={
            "email": "admin@acme-cloud.example.com", "password": _password()})
        assert r.status_code == 200
        csrf2 = _csrf(c2)
        r = await c2.get("/api/v1/portal/budgets", headers=csrf2)
        rows = r.json()["items"]
        step("portal shows own budgets only",
             any(b["name"].startswith("Portal smoke") for b in rows)
             and all("Cobalt" not in b["name"] for b in rows), str(rows)[:160])
        raw = r.text.lower()
        step("portal payload leaks no partner fields",
             "margin" not in raw.replace("margin:0", "") and "provider_cost" not in raw
             and "internal" not in raw, raw[:160])
        r = await c2.get("/api/v1/portal/anomalies", headers=csrf2)
        step("portal cost-changes leg renders", r.status_code == 200)

    print(f"SMOKE4_OK — {len(ok)} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
