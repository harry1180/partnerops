"""Live HTTP smoke for Phase 2 operations (requires API on :8001 + seeded DB).

Journey: issued invoice -> credit note (draft, issue, invoice corrected,
PDF renders) -> material exception -> waiver request -> DIFFERENT user
approves -> mark waived -> period close succeeds -> credit tracked +
allocated -> commitment coverage -> reports run -> approvals visible to
auditor. Maker-checker negatives included.

Run:  .venv/Scripts/python.exe tools/smoke_phase2_live.py [base_url]
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
    ok = []
    tag = str(int(__import__("time").time()))[-6:]
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/v1/auth/login", json={
            "email": "msp@northwind-msp.example.com", "password": _password()})
        assert r.status_code == 200, r.text
        csrf = _csrf(c)
        ok.append("login msp")

        # ensure June data + an issued invoice exist (reuse phase-1 flow fast-path)
        r = await c.get("/api/v1/customers?search=ACME")
        acme = next(i for i in r.json()["items"] if i["code"] == "ACME")
        cid = acme["id"]
        r = await c.get("/api/v1/invoices", params={"page": 1, "page_size": 50})
        CORRECTABLE = ("issued", "exported", "paid_or_settled", "disputed")
        issued = [i for i in r.json()["items"]
                  if i["customer_id"] == cid and i["status"] in CORRECTABLE]
        if not issued:
            # self-heal: price July for ACME with an existing active contract, issue it
            r = await c.get("/api/v1/contracts", params={"customer_id": cid})
            active_cv = next(v for x in r.json() for v in x["versions"] if v["status"] == "active")
            r = await c.post("/api/v1/pricing/runs", json={
                "customer_id": cid, "contract_version_id": active_cv["id"],
                "period_start": "2026-07-01T00:00:00+00:00",
                "period_end": "2026-08-01T00:00:00+00:00"}, headers=csrf)
            assert r.status_code == 202, f"self-heal pricing: {r.status_code} {r.text[:200]}"
            r = await c.post("/api/v1/invoices", json={"run_id": r.json()["run_id"]}, headers=csrf)
            assert r.status_code == 201, f"self-heal invoice: {r.status_code} {r.text[:200]}"
            inv = r.json()
            for to_status in ("under_review", "approved", "issued"):
                rr = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                                  json={"to_status": to_status}, headers=csrf)
                assert rr.status_code == 200, f"self-heal {to_status}: {rr.status_code} {rr.text[:200]}"
            issued = [inv]
        inv_id = issued[0]["id"]
        ok.append(f"target invoice {issued[0]['invoice_number']} ({issued[0]['status']})")

        # --- credit note draft + issue ------------------------------------
        r = await c.post("/api/v1/billing-notes", json={
            "invoice_id": inv_id, "kind": "credit",
            "lines": [{"description": "Overcharge correction", "amount": "42.50"}],
            "reason": "Smoke: duplicated support line corrected via credit note",
        }, headers=csrf)
        assert r.status_code == 201, f"note draft: {r.status_code} {r.text[:200]}"
        note = r.json()
        assert Decimal(note["amount"]) == Decimal("42.50")
        ok_steps_note = note["note_number"]
        ok.append(f"credit note drafted {ok_steps_note}")

        r = await c.get(f"/api/v1/billing-notes/{note['id']}/download?fmt=pdf")
        assert r.status_code == 200 and r.content[:4] == b"%PDF"
        ok.append(f"note PDF renders ({len(r.content)}B)")

        r = await c.post(f"/api/v1/billing-notes/{note['id']}/issue", json={}, headers=csrf)
        assert r.status_code == 200, f"note issue: {r.status_code} {r.text[:200]}"
        assert r.json()["invoice_status"] == "corrected"
        ok.append("note issued; invoice -> corrected")

        # corrected invoice is still immutable
        r = await c.post(f"/api/v1/invoices/{inv_id}/transition",
                         json={"to_status": "draft"}, headers=csrf)
        assert r.status_code == 409
        ok.append("corrected invoice immutable (409)")

        # --- waiver -> maker-checker -> period close ----------------------
        # ensure a material open exception exists for June
        r = await c.post("/api/v1/reconciliation/runs", json={
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00",
            "tolerance_abs": "0.000001"}, headers=csrf)
        assert r.status_code == 201, r.text[:200]
        rec = r.json()
        run_id = rec["run_id"]
        r = await c.get("/api/v1/reconciliation/exceptions",
                        params={"status": "open", "page": 1, "page_size": 100})
        # the close gate scopes to the LATEST completed run — waive its blockers
        material = [e for e in r.json()["items"]
                    if e["materiality"] == "material" and e["run_id"] == run_id]
        ok.append(f"recon: {len(material)} material open exceptions (latest run)")

        # close attempt should be blocked while material exceptions are open
        r = await c.post("/api/v1/period-closes/close", json={
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00"}, headers=csrf)
        already_closed = r.status_code == 409 and \
            r.json().get("detail", {}).get("code") == "already_closed"
        if already_closed:
            ok.append("June already closed from a prior run (idempotent)")
        elif material:
            assert r.status_code == 409 and r.json()["detail"]["code"] == "material_exceptions_open"
            ok.append("period close blocked by material exceptions (409)")
        else:
            ok.append("no material exceptions this period (close gate trivially passes)")

        if material:
            exc_id = material[0]["id"]
            r = await c.post(f"/api/v1/reconciliation/exceptions/{exc_id}/waive", json={
                "exception_id": exc_id,
                "reason": "Smoke waiver: traced to provider rounding, immaterial to customers",
            }, headers=csrf)
            assert r.status_code == 201, f"waive request: {r.status_code} {r.text[:200]}"
            appr_id = r.json()["id"]
            ok.append(f"waiver approval requested {r.json()['request_number']}")

            # maker cannot self-approve: new client, same user is maker -> 403
            r = await c.post(f"/api/v1/approvals/{appr_id}/decide?decision=approved",
                             json={"note": "trying to self-approve"}, headers=csrf)
            assert r.status_code == 403, f"self-approve should 403, got {r.status_code}"
            ok.append("self-approval rejected (403)")

            # checker approves
            async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c2:
                r = await c2.post("/api/v1/auth/login", json={
                    "email": "dist@northwind-distribution.example.com", "password": _password()})
                assert r.status_code == 200
                csrf2 = _csrf(c2)
                r = await c2.post(f"/api/v1/approvals/{appr_id}/decide?decision=approved",
                                  json={"note": "Reviewed: acceptable to waive"}, headers=csrf2)
                assert r.status_code == 200, f"checker approve: {r.status_code} {r.text[:200]}"
                assert r.json()["status"] == "approved"
                # checker applies the waiver
                r = await c2.post(
                    f"/api/v1/reconciliation/exceptions/{exc_id}/mark-waived",
                    json={}, headers=csrf2)
                assert r.status_code == 200 and r.json()["status"] == "waived"
            ok.append("checker approved + waiver applied")

            # close now succeeds (waive all remaining material first)
            for e in material[1:]:
                r = await c.post(f"/api/v1/reconciliation/exceptions/{e['id']}/waive", json={
                    "exception_id": e["id"], "reason": "Smoke waiver for remaining material exception"},
                                 headers=csrf)
                assert r.status_code == 201
                aid = r.json()["id"]
                async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c3:
                    await c3.post("/api/v1/auth/login", json={
                        "email": "dist@northwind-distribution.example.com", "password": _password()})
                    csrf3 = _csrf(c3)
                    r = await c3.post(f"/api/v1/approvals/{aid}/decide?decision=approved",
                                      json={"note": "Reviewed, acceptable"}, headers=csrf3)
                    assert r.status_code == 200, r.text[:200]
                    r = await c3.post(
                        f"/api/v1/reconciliation/exceptions/{e['id']}/mark-waived",
                        json={}, headers=csrf3)
                    assert r.status_code == 200
            r = await c.post("/api/v1/period-closes/close", json={
                "period_start": "2026-06-01T00:00:00+00:00",
                "period_end": "2026-07-01T00:00:00+00:00"}, headers=csrf)
            assert r.status_code in (201, 409), f"close: {r.status_code} {r.text[:250]}"
            ok.append("period closed after waivers" if r.status_code == 201 else "close idempotent")

        # --- credits ------------------------------------------------------
        r = await c.post("/api/v1/credits", json={
            "display_name": f"Smoke credit {tag}", "kind": "promotional",
            "amount_total": "500.00"}, headers=csrf)
        assert r.status_code == 201, f"credit: {r.status_code} {r.text[:200]}"
        credit_id = r.json()["id"]
        r = await c.post(f"/api/v1/credits/{credit_id}/allocate", json={
            "customer_id": cid, "amount": "200.00"}, headers=csrf)
        assert r.status_code == 200 and r.json()["allocation_status"] == "partially_allocated"
        r = await c.post(f"/api/v1/credits/{credit_id}/allocate", json={
            "customer_id": cid, "amount": "400.00"}, headers=csrf)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "over_allocation"
        ok.append("credit tracked + allocated; over-allocation rejected")

        # --- commitments + coverage --------------------------------------
        r = await c.post("/api/v1/commitments", json={
            "kind": "aws_savings_plan", "display_name": f"Smoke SP {tag}",
            "start_date": "2026-06-01T00:00:00+00:00",
            "end_date": "2027-06-01T00:00:00+00:00",
            "hourly_commitment": "0.50"}, headers=csrf)
        assert r.status_code == 201, f"commitment: {r.status_code} {r.text[:200]}"
        r = await c.get("/api/v1/commitments/coverage", params={
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00"})
        assert r.status_code == 200
        cov = r.json()
        assert Decimal(cov["commitment_savings"]) > 0
        ok.append(f"coverage: {cov['coverage_pct']}% covered, savings {cov['commitment_savings']}")

        # --- reports -------------------------------------------------------
        for key in ("customer_cost_statement", "invoice_summary", "margin_by_customer",
                    "revenue_leakage", "tag_compliance"):
            r = await c.post(f"/api/v1/reports/{key}/run", json={}, params={"fmt": "csv"})
            assert r.status_code == 200, f"report {key}: {r.status_code} {r.text[:150]}"
        ok.append("5 CSV reports generated")

        # --- leakage endpoint ----------------------------------------------
        r = await c.get("/api/v1/revenue-leakage")
        assert r.status_code == 200
        ok.append("revenue-leakage endpoint ok")

        # --- approvals list + audit -----------------------------------------
        r = await c.get("/api/v1/approvals", params={"page": 1, "page_size": 50})
        assert r.status_code == 200 and r.json()["total"] >= 1
        r = await c.get("/api/v1/audit-events", params={"page": 1, "page_size": 200})
        acts = {e["action"] for e in r.json()["items"]}
        for needed in ("billing_note.created", "billing_note.issued",
                       "approval.requested", "approval.granted", "credit.created",
                       "credit.allocated", "commitment.created", "export.data"):
            assert needed in acts, f"missing audit action: {needed}"
        ok.append("audit trail covers all phase-2 actions")

    print("\n".join(f"  \u2714 {s}" for s in ok))
    print("SMOKE2_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
