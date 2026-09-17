"""Live HTTP smoke for the Phase 1 workflow (requires API on :8001 + seeded DB).

Not a unit test — a demo rehearsal of the charter journey over the wire:
login → load synthetic AWS data → contract + rules → pricing → invoice
(lifecycle) → PDF/CSV download → reconciliation → tenant isolation probe.

Run:  .venv/Scripts/python.exe tools/smoke_phase1_live.py [base_url]
Exit 0 = every step passed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APP_ENV", "local")

import httpx  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
PASSWORD = None  # from settings


def _password() -> str:
    from app.core.config import get_settings

    return get_settings().seed_demo_password


def _csrf(client: httpx.AsyncClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("cpo_csrf") or ""}


async def main() -> int:
    import time
    suffix = str(int(time.time()))[-6:]
    ok_steps = []
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        # 1. login
        r = await c.post("/api/v1/auth/login", json={
            "email": "msp@northwind-msp.example.com", "password": _password(),
        })
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        me = r.json()["user"]
        csrf = _csrf(c)
        ok_steps.append(f"login msp_admin org={me['org_kind']}")

        # 2. ensure synthetic billing loaded (idempotent)
        r = await c.post("/api/v1/ingestion/synthetic/load", data={"months": "2026-06,2026-07,2026-08"},
                         headers={**csrf, "content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 201, f"synthetic load: {r.status_code} {r.text}"
        results = r.json()["results"]
        canonical = sum(x["canonical"] for x in results)
        unmapped = sorted({a for x in results for a in x["unmapped_accounts"]})
        ok_steps.append(f"ingest: {canonical} canonical rows, unmapped={unmapped}")

        # 3. find the Acme customer
        r = await c.get("/api/v1/customers?search=Acme")
        assert r.status_code == 200
        acme = next((i for i in r.json()["items"] if i["code"] == "ACME"), None)
        assert acme, "Acme customer missing from seed"
        cust_id = acme["id"]
        ok_steps.append(f"customer {acme['name']} ({acme['account_family_count']} families)")

        # 4. contract + rule via API
        r = await c.post("/api/v1/contracts", json={
            "customer_id": cust_id, "code": f"S{suffix}", "name": f"Smoke Contract S{suffix}",
            "effective_start": "2026-06-01T00:00:00+00:00",
            "minimum_monthly": "1500.00",
        }, headers=csrf)
        assert r.status_code == 201, f"contract: {r.status_code} {r.text}"
        contract_id, version_id = r.json()["id"], r.json()["version_id"]
        ok_steps.append("contract created (v1 draft)")

        r = await c.post("/api/v1/billing-rules", json={
            "contract_id": contract_id, "code": f"S{suffix}-MARKUP", "name": "12% markup",
            "rule_type": "percentage_markup",
        }, headers=csrf)
        assert r.status_code == 201, r.text
        rule_id = r.json()["id"]
        rv_id = r.json()["version_id"]
        # set parameters via a new version carrying them (v1 was an empty draft)
        r = await c.post(f"/api/v1/billing-rules/{rule_id}/versions", json={
            "parameters": {"type": "percentage_markup", "percent": "12"},
            "priority": 10, "calc_order": 10,
        }, headers=csrf)
        assert r.status_code == 201, r.text
        rv2 = r.json()["id"]
        r = await c.post(f"/api/v1/billing-rule-versions/{rv2}/publish", headers=csrf)
        assert r.status_code == 200, f"publish: {r.status_code} {r.text}"
        ok_steps.append("rule v2 published (12% markup)")

        # bind the published rule version into the draft contract version
        r = await c.patch(f"/api/v1/contract-versions/{version_id}/bindings", json={
            "rule_bindings": [{"rule_id": rule_id, "rule_version_id": rv2, "order": 1}],
        }, headers=csrf)
        assert r.status_code == 200, f"bindings: {r.status_code} {r.text}"
        ok_steps.append("rule bound to contract v1")

        r = await c.post(f"/api/v1/contract-versions/{version_id}/activate", headers=csrf)
        assert r.status_code == 200, f"activate: {r.status_code} {r.text}"
        ok_steps.append("contract v1 activated")

        # 5. pricing for June
        r = await c.post("/api/v1/pricing/runs", json={
            "customer_id": cust_id, "contract_version_id": version_id,
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00",
        }, headers=csrf)
        assert r.status_code == 202, f"pricing: {r.status_code} {r.text}"
        prun = r.json()
        totals = prun["totals"]
        ok_steps.append(f"pricing completed: provider={totals.get('provider_cost')} "
                        f"billed={totals.get('customer_subtotal')} margin={totals.get('margin')}")

        # 6. invoice
        r = await c.post("/api/v1/invoices", json={"run_id": prun["run_id"]}, headers=csrf)
        assert r.status_code == 201, f"invoice: {r.status_code} {r.text}"
        inv = r.json()
        ok_steps.append(f"invoice {inv['invoice_number']} total={inv['total']}")

        r = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                         json={"to_status": "under_review"}, headers=csrf)
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                         json={"to_status": "approved"}, headers=csrf)
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                         json={"to_status": "issued"}, headers=csrf)
        assert r.status_code == 200, r.text
        ok_steps.append("invoice lifecycle: review→approved→issued")

        # immutability over HTTP
        r = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                         json={"to_status": "draft"}, headers=csrf)
        assert r.status_code == 409, f"immutability: expected 409, got {r.status_code}"
        ok_steps.append("issued→draft correctly rejected (409)")

        # 7. documents
        r = await c.get(f"/api/v1/invoices/{inv['id']}/download?fmt=pdf")
        assert r.status_code == 200 and r.content[:4] == b"%PDF", f"pdf: {r.status_code}"
        r2 = await c.get(f"/api/v1/invoices/{inv['id']}/download?fmt=csv")
        assert r2.status_code == 200 and inv["invoice_number"].encode() in r2.content
        ok_steps.append(f"PDF {len(r.content)}B + CSV {len(r2.content)}B render")

        # 8. reconciliation (June has the 999 account → exceptions expected)
        r = await c.post("/api/v1/reconciliation/runs", json={
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00",
            "tolerance_abs": "0.000001",
        }, headers=csrf)
        assert r.status_code == 201, f"recon: {r.status_code} {r.text}"
        rec = r.json()
        ok_steps.append(f"reconciliation: {rec['exceptions_open']} exceptions, "
                        f"{rec['material_open']} material")

        # 9. data-quality dashboard
        r = await c.get("/api/v1/data-quality")
        assert r.status_code == 200
        dq = r.json()
        ok_steps.append(f"data-quality: periods={dq['billing_periods_present']}, "
                        f"quarantined={dq['quarantined_by_reason']}, "
                        f"unmapped={len(dq['unmapped_usage'])}")

        # 10. usage explorer (grouped, paged)
        r = await c.get("/api/v1/usage/explorer?group_by=cost_category&period_start=2026-06-01T00:00:00Z")
        assert r.status_code == 200
        grp = r.json()
        ok_steps.append(f"usage explorer: {len(grp['items'])} groups")

        # 11. customer-role isolation probe: viewer of ANOTHER tenant's invoice
        r = await c.get(f"/api/v1/invoices/{inv['id']}")
        assert r.status_code == 200 and r.json().get("margin_total") is not None
        # (msp sees margin; the other-tenant check is done in Playwright/PG tests)
        ok_steps.append("msp sees margin_total on own invoice")

        # 12. rule sandbox (read-only preview, stored as evidence)
        r = await c.post(f"/api/v1/billing-rule-versions/{rv2}/sandbox",
                         json={"period_start": "2026-06-01T00:00:00+00:00",
                               "period_end": "2026-07-01T00:00:00+00:00"},
                         headers=csrf)
        assert r.status_code == 200, f"sandbox: {r.status_code} {r.text[:300]}"
        sb = r.json()
        assert float(sb["delta"]) != 0.0
        ok_steps.append(f"sandbox: {sb['baseline_total']} -> {sb['sandbox_total']} (delta {sb['delta']})")

        # 13. margins (partner only)
        r = await c.get("/api/v1/margins/summary",
                        params={"period_start": "2026-06-01T00:00:00+00:00"})
        assert r.status_code == 200
        mg = r.json()["totals"]
        assert float(mg["revenue"]) > 0
        ok_steps.append(f"margins: revenue={mg['revenue']} margin={mg['margin']} "
                        f"neg_margin_customers={mg['negative_margin_customers']}")

        # 14. invoice lineage endpoint (per line: amount -> source records + rules + run)
        r = await c.get(f"/api/v1/invoices/{inv['id']}")
        first_line = r.json()["lines"][0]
        r = await c.get(f"/api/v1/invoices/{inv['id']}/lineage",
                        params={"line_number": first_line["line_number"]})
        assert r.status_code == 200, f"lineage: {r.status_code} {r.text[:200]}"
        lin = r.json()
        assert lin["run_id"] and lin["contract_version_id"]
        assert "amount" in lin and "formula" in lin
        ok_steps.append(f"invoice lineage: line {first_line['line_number']} -> run #{lin['run_number']}, "
                        f"{len(lin['source_record_ids'])} source records, engine {lin['engine_version']}")

        # 15. second client: customer admin sees portal, NOT partner data
        async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c2:
            r = await c2.post("/api/v1/auth/login", json={
                "email": "admin@acme-cloud.example.com", "password": _password()})
            assert r.status_code == 200, f"cust login: {r.status_code} {r.text}"
            csrf2 = _csrf(c2)

            r = await c2.get("/api/v1/margins/summary")
            assert r.status_code == 403, f"margin leak: {r.status_code}"
            r = await c2.get("/api/v1/data-quality")
            assert r.status_code == 403
            r = await c2.get("/api/v1/reconciliation/exceptions")
            assert r.status_code == 403
            ok_steps.append("customer admin 403 on margin/dq/recon endpoints")

            r = await c2.get("/api/v1/portal/customer")
            assert r.status_code == 200 and r.json()["code"] == "ACME"
            r = await c2.get("/api/v1/portal/usage/summary", params={"group_by": "service"})
            assert r.status_code == 200 and r.json()["items"]
            ok_steps.append("portal customer + usage")

            r = await c2.get("/api/v1/portal/invoices")
            assert r.status_code == 200
            all_rows = r.json()
            mine = [i for i in all_rows if i["invoice_number"] == inv["invoice_number"]]
            assert mine and mine[0]["status"] == "issued", "this run's invoice not visible in portal"
            ok_steps.append(f"portal shows issued invoice ({len(all_rows)} issued visible to customer)")

            # no partner economics leak through portal invoice payload
            assert "margin_total" not in mine[0] and "provider_cost" not in mine[0]
            r = await c2.get(f"/api/v1/portal/invoices/{mine[0]['id']}")
            assert r.status_code == 200 and "margin" not in str(r.json()).lower().replace("margin_note", "")
            ok_steps.append("portal invoice payload free of margin fields")

            # dispute
            r = await c2.post("/api/v1/disputes", json={
                "invoice_id": mine[0]["id"], "subject": "Duplicate EBS rate suspected",
                "description": "Looks like a double charge on Aug 14.",
                "amount_disputed": "55.00"}, headers=csrf2)
            assert r.status_code == 201, f"dispute: {r.status_code} {r.text[:300]}"
            dispute = r.json()
            ok_steps.append(f"dispute filed {dispute['dispute_number']}")

            # customer cannot resolve own dispute
            r = await c2.post(f"/api/v1/disputes/{dispute['id']}/resolve", json={
                "status": "resolved_accepted", "resolution_notes": "no permission"}, headers=csrf2)
            assert r.status_code == 403, f"customer resolve: {r.status_code} {r.text[:120]}"
            ok_steps.append("customer cannot resolve disputes")

        # 16. partner sees and resolves the dispute
        r = await c.get("/api/v1/disputes")
        assert r.status_code == 200 and any(d["id"] == dispute["id"] for d in r.json()["items"])
        r = await c.post(f"/api/v1/disputes/{dispute['id']}/resolve", json={
            "status": "resolved_denied",
            "resolution_notes": "Traced to a late adjustment, not a duplicate (see recon run)."},
            headers=csrf)
        assert r.status_code == 200, f"resolve: {r.status_code} {r.text[:300]}"
        ok_steps.append("partner resolved dispute (rejected w/ note)")

        # 17. audit trail covers the whole journey
        acts: set[str] = set()
        for pg in range(1, 5):
            r = await c.get("/api/v1/audit-events", params={"page": pg, "page_size": 200})
            body = r.json()
            acts.update(e["action"] for e in body["items"])
            if pg * 200 >= body["total"]:
                break
        for needed in ("invoice.state_changed", "dispute.created", "dispute.resolved",
                       "reconciliation.completed", "ingestion.file_parsed"):
            assert needed in acts, f"missing audit action: {needed}"
        ok_steps.append(f"audit trail: {len(acts)} distinct actions incl. dispute lifecycle")

    print("\n".join(f"  ✔ {s}" for s in ok_steps))
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
