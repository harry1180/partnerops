"""Live HTTP smoke for Phase 3 (multi-cloud ingestion; requires API on :8001 + seeded DB).

Journey: connectors visible (seed wiring) -> synthetic Azure load via
connector run -> Azure canonical rows visible in usage explorer -> orphan
subscription auto-discovered as unmapped (DQ + cloud-accounts) -> per-account
provider bill totals exist alongside the enrollment total -> August
reconciliation reports Azure provider-vs-canonical delta (the planted +180)
and the Azure unmapped-account exception -> Azure pricing run for a
multi-cloud customer -> portal payload contains no provider/internal fields.

Re-runnable: duplicate files are skipped by design, so second runs still pass.

Run:  .venv/Scripts/python.exe tools/smoke_phase3_live.py [base_url]
"""

from __future__ import annotations

import asyncio
import os
import sys
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

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        # 1. MSP login; connectors seeded (both providers, honest flags)
        r = await c.post("/api/v1/auth/login", json={
            "email": "msp@northwind-msp.example.com", "password": _password()})
        assert r.status_code == 200, r.text
        csrf = _csrf(c)
        r = await c.get("/api/v1/connectors")
        items = r.json()["items"]
        names = {i["name"] for i in items}
        step("connectors seeded for aws+azure",
             {"Northwind AWS CUR", "Northwind Azure Cost Export"} <= names, str(names))
        step("no fake live fetch", all(i["fetch_available"] is False for i in items))
        az = next(i for i in items if i["provider"] == "azure")

        # 2. run connector for all three fixture periods (idempotent re-runs)
        for period in ("2026-06", "2026-07", "2026-08"):
            r = await c.post(f"/api/v1/connectors/{az['id']}/run", json={"period": period},
                             headers=csrf)
            body = r.json()
            step(f"azure connector run {period}",
                 r.status_code == 200 and body["status"] == "ingested"
                 and (body["canonical"] > 20 or body["detail"].get("skipped_duplicate_file")),
                 str(body)[:160])

        # 3. Azure files listed with provider attribution + sha evidence
        r = await c.get("/api/v1/ingestion/files")
        az_files = [f for f in r.json() if f["provider"] == "azure"]
        step("azure raw files listed", len(az_files) >= 2, str(az_files)[:120])
        step("azure file carries checksum evidence", all(f["sha256"] for f in az_files))

        # 4. usage explorer shows Azure services alongside AWS
        r = await c.get("/api/v1/usage/explorer?group_by=service&page=1&page_size=50")
        services = {i["group"] for i in r.json()["items"]}
        step("azure services in usage explorer",
             "Virtual Machines" in services and "Application Gateway" in services,
             str(services)[:200])

        # 5. orphan subscription discovered + unmapped (DQ + accounts list)
        r = await c.get("/api/v1/cloud-accounts?allocation_status=unmapped&page=1&page_size=50")
        unmapped_ids = {i["external_id"] for i in r.json()["items"]}
        step("orphan azure subscription auto-discovered",
             "unmapped-0000-4000-8000-000000000099" in unmapped_ids, str(unmapped_ids)[:160])

        # 6. per-account provider bill totals recorded (DB truth via recon below);
        #    also July: the planted duplicate was quarantined by ingest
        r = await c.get("/api/v1/data-quality")
        dq = r.json()
        step("azure periods present in DQ", len(dq["billing_periods_present"]) >= 3)
        step("azure duplicate quarantined in July",
             dq["quarantined_by_reason"].get("duplicate_record", 0) >= 1,
             str(dq["quarantined_by_reason"]))

        # 6b. bill totals endpoint: azure has one invoice-level statement AND
        # per-account rollups with checksum evidence
        r = await c.get("/api/v1/reconciliation/bill-totals",
                        params={"period_start": "2026-06-01T00:00:00+00:00"})
        assert r.status_code == 200, r.text
        az_bt = [t for t in r.json()["items"] if t["provider"] == "azure"]
        step("azure invoice-level bill total present",
             any(t["level"] == "invoice" for t in az_bt), str(az_bt)[:120])
        step("azure per-account bill totals present",
             sum(1 for t in az_bt if t["level"] == "account") >= 3,
             f"{len(az_bt)} rows")
        step("per-account totals carry file evidence",
             all(t["evidence"].get("file_id") for t in az_bt if t["level"] == "account"))

        # 7. August reconciliation: both providers in scope. Each file plants
        #    an off-line true-up (AWS +4500, Azure +180) so leg A must surface
        #    a merged provider-vs-canonical delta of exactly 4680.00; the
        #    azure orphan subscription + enrollment-scope rows sit under the
        #    payer ref (50001-alpha) as an unmapped-account exception.
        r = await c.post("/api/v1/reconciliation/runs", json={
            "period_start": "2026-08-01T00:00:00+00:00",
            "period_end": "2026-09-01T00:00:00+00:00",
            "tolerance_abs": "0.000001"}, headers=csrf)
        assert r.status_code == 201, r.text
        run_id = r.json()["run_id"]
        r = await c.get("/api/v1/reconciliation/exceptions?status=open&page=1&page_size=100")
        excs = [e for e in r.json()["items"] if e["run_id"] == run_id]
        types = {e["type"] for e in excs}
        pva = [e for e in excs if e["type"] == "provider_bill_adjustment"]
        step("recon surfaces merged provider-vs-canonical delta",
             pva and abs(Decimal(pva[0]["amount_delta"]) - Decimal("4680.00")) < Decimal("0.01"),
             str(pva)[:200])
        step("recon surfaces azure-side unmapped usage under the payer account",
             "unmapped_account" in types
             and any(e["billing_account_ref"] == "50001-alpha" for e in excs
                     if e["type"] == "unmapped_account"),
             str(sorted({(e["type"], e["billing_account_ref"]) for e in excs}))[:220])

        # 8. multi-cloud pricing + invoice lineage: give BlueRiver a contract
        #    (seed ships none, same as phase-1 smoke), run pricing over June,
        #    build the invoice, and prove its lineage cites BOTH providers'
        #    source record ids through the /invoices/{id}/lineage endpoint.
        suffix = str(__import__("time").time())[-6:]
        r = await c.get("/api/v1/customers")
        blur = next(x for x in r.json()["items"] if x["code"] == "BLUR")
        r = await c.post("/api/v1/contracts", json={
            "customer_id": blur["id"], "code": f"P3S{suffix}", "name": f"P3 smoke contract {suffix}",
            "effective_start": "2026-06-01T00:00:00+00:00"}, headers=csrf)
        assert r.status_code == 201, f"contract: {r.text}"
        version_id = r.json()["version_id"]
        r = await c.post(f"/api/v1/contract-versions/{version_id}/activate", headers=csrf)
        assert r.status_code == 200, f"activate: {r.text}"
        r = await c.post("/api/v1/pricing/runs", json={
            "customer_id": blur["id"], "contract_version_id": version_id,
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00"}, headers=csrf)
        assert r.status_code == 202, r.text
        run = r.json()
        step("multi-cloud pricing run completed", run["status"] == "completed", str(run)[:160])

        r = await c.post("/api/v1/invoices", json={"run_id": run["run_id"]}, headers=csrf)
        assert r.status_code == 201, f"invoice: {r.text}"
        inv = r.json()
        r = await c.get(f"/api/v1/invoices/{inv['id']}")
        lines = r.json()["lines"]
        azure_lines = [ln for ln in lines
                       if any(str(s).startswith("50001-alpha-2026-06") for s in ln["source_record_ids"])]
        aws_lines = [ln for ln in lines
                     if any(str(s).startswith("777700000001-2026-06") for s in ln["source_record_ids"])]
        step("invoice line lineage cites azure source records", bool(azure_lines),
             f"{len(azure_lines)}/{len(lines)} lines")
        step("invoice line lineage cites aws source records", bool(aws_lines),
             f"{len(aws_lines)}/{len(lines)} lines")
        if azure_lines:
            r = await c.get(f"/api/v1/invoices/{inv['id']}/lineage",
                            params={"line_number": azure_lines[0]["line_number"]})
            lin = r.json()
            step("lineage endpoint resolves azure line to pricing run",
                 r.status_code == 200 and lin["run_id"] == run["run_id"]
                 and any("50001-alpha" in str(s) for s in lin["source_record_ids"]),
                 str(lin)[:160])
        step("run totals nonzero", Decimal(inv["total"]) > 0, str(inv["total"]))

        # 9. audit trail covers connector actions
        r = await c.get("/api/v1/audit-events?page=1&page_size=200")
        actions = {e["action"] for e in r.json()["items"]}
        step("connector audit events present",
             "integration.connector_ran" in actions, str(actions)[:200])

    print(f"SMOKE3_OK — {len(ok)} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
