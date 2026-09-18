"""Live HTTP smoke for Phase 5 (assistant + integrations; API on :8001 + seeded DB).

Journey: assistant capabilities + answered query (citations) + honest refusal
+ AI audit trail visible to auditor -> webhook endpoint create (secret shown
once) -> bad-target rejection -> test event delivered to a REAL local
listener with receiver-side HMAC verification -> invoice.issued event queues
+ delivers signed -> dispute.created event queued -> ERP export (json+csv)
contains no partner fields and is audited -> scoped API token: Bearer read
allowed, out-of-scope refused -> integration record reports unconnected
honestly -> portal assistant leg (customer asks, gets own-scope answer;
partner-only intent refused).

Re-runnable: unique names per run; deliveries append-only.

Run:  .venv/Scripts/python.exe tools/smoke_phase5_live.py [base_url]
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APP_ENV", "local")

import httpx  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
WEB = "http://localhost:3000"  # noqa: F841  (parity with other smokes; UI legs use API)

steps: list[str] = []


def step(msg: str) -> None:
    steps.append(msg)
    print(f"  ok: {msg}")


def _password() -> str:
    from app.core.config import get_settings

    return get_settings().seed_demo_password


def _csrf(client: httpx.AsyncClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("cpo_csrf") or ""}


# ---------- real local receiver ----------

received: list[dict] = []


class _H(BaseHTTPRequestHandler):
    secret = ""

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(n)
        ts = self.headers.get("X-CPPartnerOps-Timestamp", "")
        sig = self.headers.get("X-CPPartnerOps-Signature", "")
        expect = "sha256=" + hmac.new(self.secret.encode(), f"{ts}.".encode() + body,
                                      hashlib.sha256).hexdigest()
        payload = json.loads(body)
        received.append({"payload": payload, "sig_ok": hmac.compare_digest(sig, expect),
                         "idem": self.headers.get("Idempotency-Key", "")})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


async def main() -> int:
    tag = uuid.uuid4().hex[:6].upper()

    # API-token client (cookie-less) helper
    def bare(token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=BASE, timeout=60.0,
                                 headers={"Authorization": f"Bearer {token}"})

    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "msp@northwind-msp.example.com",
                               "password": _password()})
        assert r.status_code == 200, r.text
        h = _csrf(c)

        r = await c.post("/api/v1/ingestion/synthetic/load",
                         data={"months": "2026-06,2026-07,2026-08"}, headers=h)
        assert r.status_code in (200, 201), r.text
        step("demo data ready (AWS synthetic)")

        # --- assistant ----------------------------------------------------
        r = await c.get("/api/v1/assistant/capabilities", headers=h)
        assert r.status_code == 200
        caps = r.json()
        assert caps["mode"] == "deterministic_demo" and not caps["can_write"]
        step(f"assistant capabilities: {len(caps['intents'])} intents, mode={caps['mode']}")

        r = await c.post("/api/v1/credits", json={
            "display_name": f"P5 credit {tag}", "kind": "promotional",
            "amount_total": "321.00"}, headers=h)
        assert r.status_code == 201, r.text
        r = await c.post("/api/v1/assistant/ask", json={
            "question": "Which credits have not been allocated?"}, headers=h)
        a = r.json()
        assert not a["refused"] and a["intent"] == "unallocated_credits"
        assert a["citations"] and any(x["type"] == "credit" for x in a["citations"])
        assert f"P5 credit {tag}" in json.dumps(a["facts"])
        step("assistant answered unallocated-credits with citations incl. planted credit")

        r = await c.post("/api/v1/assistant/ask",
                         json={"question": "what is the weather in paris"}, headers=h)
        a2 = r.json()
        assert a2["refused"] and a2["refusal_reason"]
        step(f"assistant refused off-domain question: '{a2['refusal_reason']}'")

        # audit trail visible to auditor role
        async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as ca:
            r = await ca.post("/api/v1/auth/login", json={
                "email": "auditor@cloudpartnerops.example.com", "password": _password()})
            assert r.status_code == 200
            r = await ca.get("/api/v1/assistant/audit?page_size=30", headers=_csrf(ca))
            rows = r.json()["items"]
            assert any(x["intent"] == "unallocated_credits" and not x["refused"] for x in rows)
            assert any(x["refused"] and "weather" in x["question"] for x in rows)
            assert all(x["mode"] == "deterministic_demo" for x in rows)
        step("AI query audit shows answered + refused queries to auditor")

        # --- webhooks -----------------------------------------------------
        r = await c.post("/api/v1/integrations/webhooks", json={
            "url": f"http://127.0.0.1:{port}/hook",
            "events": ["invoice.issued", "dispute.created", "integration.test"],
            "description": f"P5 smoke {tag}"}, headers=h)
        assert r.status_code == 201, r.text
        ep_id, secret = r.json()["id"], r.json()["signing_secret"]
        _H.secret = secret
        assert secret.startswith("whsec_")
        step("webhook endpoint created, secret shown once")

        r = await c.post("/api/v1/integrations/webhooks", json={
            "url": "http://169.254.169.254/latest/meta-data", "events": ["invoice.issued"]},
            headers=h)
        assert r.status_code == 422
        step("SSRF: metadata target refused (422)")

        r = await c.get("/api/v1/integrations/overview", headers=h)
        assert secret not in json.dumps(r.json())
        step("overview masks the signing secret")

        r = await c.post(f"/api/v1/integrations/webhooks/{ep_id}/test", json={}, headers=h)
        assert r.status_code == 200 and r.json()["queued"] >= 1
        await asyncio.sleep(0.5)
        mine = [x for x in received if x["payload"].get("event") == "integration.test"]
        assert mine and mine[-1]["sig_ok"] and mine[-1]["idem"], "test delivery must verify"
        step("integration.test delivered to REAL listener; HMAC verified receiver-side")

        # --- issue an invoice -> invoice.issued event ----------------------
        r = await c.get("/api/v1/customers?search=Acme", headers=h)
        acme = next(i for i in r.json()["items"] if i["code"] == "ACME")
        r = await c.get(f"/api/v1/contracts?customer_id={acme['id']}", headers=h)
        active_cv = None
        for x in r.json():
            for v in x["versions"]:
                if v["status"] == "active":
                    active_cv = v
        assert active_cv, "demo Acme needs an active contract (run phase-1/2 smokes first)"
        r = await c.post("/api/v1/pricing/runs", json={
            "customer_id": acme["id"], "contract_version_id": active_cv["id"],
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00"}, headers=h)
        assert r.status_code == 202, r.text
        prun = r.json()
        r = await c.post("/api/v1/invoices", json={"run_id": prun["run_id"]}, headers=h)
        assert r.status_code == 201, r.text
        inv = r.json()
        for st in ("under_review", "approved"):
            rr = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                              json={"to_status": st}, headers=h)
            if rr.status_code == 409 and "approval" in rr.text.lower():
                ra = await c.get("/api/v1/approvals?status=pending", headers=h)
                for ap in ra.json()["items"]:
                    if str(inv["id"]) in json.dumps(ap):
                        await c.post(f"/api/v1/approvals/{ap['id']}/decide?decision=approved",
                                     headers=h)
                rr = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                                  json={"to_status": st}, headers=h)
            assert rr.status_code == 200, f"{st}: {rr.text}"
        # issue (may require a second approver in maker-checker demo orgs;
        # fall back to platform admin approval if blocked)
        rr = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                          json={"to_status": "issued"}, headers=h)
        if rr.status_code != 200:
            async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as cp:
                await cp.post("/api/v1/auth/login", json={
                    "email": "admin@cloudpartnerops.example.com", "password": _password()})
                # approve pending, then MSP issues
                ra = await cp.get("/api/v1/approvals?status=pending", headers=_csrf(cp))
                for ap in ra.json()["items"]:
                    if str(inv["id"]) in json.dumps(ap):
                        await cp.post(f"/api/v1/approvals/{ap['id']}/decide?decision=approved",
                                      headers=_csrf(cp))
            rr = await c.post(f"/api/v1/invoices/{inv['id']}/transition",
                              json={"to_status": "issued"}, headers=h)
        assert rr.status_code == 200, f"issue: {rr.text}"
        await c.post(f"/api/v1/integrations/webhooks/deliver?endpoint_id={ep_id}", headers=h)
        await asyncio.sleep(0.3)
        issued = [x for x in received
                  if x["payload"].get("event") == "invoice.issued"
                  and x["payload"]["data"]["invoice_number"] == inv["invoice_number"]]
        assert issued, "invoice.issued must reach the listener"
        assert issued[-1]["sig_ok"], "signature must verify"
        step(f"invoice.issued delivered signed (idempotency-key {issued[-1]['idem'][:8]}…)")

        # --- dispute event --------------------------------------------------
        r = await c.post("/api/v1/disputes", json={
            "invoice_id": inv["id"], "subject": f"P5 smoke dispute {tag}",
            "amount_disputed": "1.00"}, headers=h)
        assert r.status_code in (200, 201), r.text
        await c.post(f"/api/v1/integrations/webhooks/deliver?endpoint_id={ep_id}", headers=h)
        await asyncio.sleep(0.3)
        dsp = [x for x in received if x["payload"].get("event") == "dispute.created"
               and x["payload"]["data"]["subject"] == f"P5 smoke dispute {tag}"]
        assert dsp and dsp[-1]["sig_ok"]
        step("dispute.created delivered signed")

        # --- ERP export -----------------------------------------------------
        r = await c.get(f"/api/v1/invoices/{inv['id']}/export?fmt=json", headers=h)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["schema"] == "cpo.erp.v1" and doc["document_number"] == inv["invoice_number"]
        blob = json.dumps(doc).lower()
        for banned in ("provider_cost", "margin", "internal_note"):
            assert banned not in blob
        r = await c.get(f"/api/v1/invoices/{inv['id']}/export?fmt=csv", headers=h)
        assert r.status_code == 200 and "section,key,field,value" in r.text
        r = await c.get("/api/v1/audit-events?page_size=30", headers=h)
        assert any(e["action"] == "export.generated" for e in r.json()["items"])
        step("ERP export (json+csv): customer-visible only, audited")

        # --- scoped API token ------------------------------------------------
        me = (await c.get("/api/v1/auth/me", headers=h)).json()
        r = await c.post("/api/v1/admin/tokens", json={
            "name": f"P5 {tag}", "org_id": me["org_id"], "scopes": ["cost.read"]}, headers=h)
        assert r.status_code == 201, r.text
        raw = r.json()["token"]
        async with bare(raw) as cb:
            r = await cb.get("/api/v1/finops/unit-economics")
            assert r.status_code == 200, r.text
            r = await cb.get("/api/v1/margins/summary")
            assert r.status_code == 403
        step("Bearer token: cost.read honored, margin refused (scope ∩ user)")

        # --- honest integration record ----------------------------------------
        r = await c.post("/api/v1/integrations", json={
            "kind": "erp", "name": f"ERP {tag}", "config": {}}, headers=h)
        assert r.status_code == 201 and r.json()["connected"] is False
        r = await c.post(f"/api/v1/integrations/{r.json()['id']}/check", headers=h)
        assert r.json()["connected"] is False and r.json()["detail"]
        step("ERP integration reports not-connected honestly (no fake ping)")

        # --- portal assistant leg ---------------------------------------------
        async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as cc:
            r = await cc.post("/api/v1/auth/login", json={
                "email": "admin@acme-cloud.example.com", "password": _password()})
            assert r.status_code == 200, r.text
            hc = _csrf(cc)
            r = await cc.post("/api/v1/assistant/ask", json={
                "question": "Which customers are below target margin?"}, headers=hc)
            assert r.status_code == 200
            a3 = r.json()
            assert a3["refused"] and "margin" in (a3["refusal_reason"] or "").lower()
            r = await cc.post("/api/v1/assistant/ask", json={
                "question": "Summarize our cost anomalies"}, headers=hc)
            a4 = r.json()
            assert not a4["refused"] and a4["intent"] == "summarize_anomalies"
            assert "acme" in a4["text"].lower() or not a4["facts"]
            blob = json.dumps(a4["facts"]).lower()
            assert "provider_cost" not in blob and "northwind" not in blob
            # portal user cannot read the AI audit trail
            r = await cc.get("/api/v1/assistant/audit")
            assert r.status_code == 403
        step("portal assistant: own-scope answers, partner intents refused, audit 403")

    srv.shutdown()
    print(f"\nSMOKE5_OK — {len(steps)} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
