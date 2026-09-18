"""Phase 5: assistant (deterministic, permission-aware, audited) and
integrations (webhook signing/validation, ERP export, scoped tokens).

Pure-function coverage first, then API flows against the seeded demo over
the real login path (RBAC + CSRF + org scope exercised end-to-end).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.services import webhooks as wh


def _h(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


async def _login_csrf(client, email: str) -> str:
    settings = get_settings()
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": settings.seed_demo_password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


async def _ensure_demo_data(client, csrf: str) -> None:
    r = await client.post("/api/v1/ingestion/synthetic/load",
                          data={"months": "2026-06,2026-07,2026-08"}, headers=_h(csrf))
    assert r.status_code in (200, 201), r.text


# ---------------- pure functions ----------------

def test_signature_roundtrip():
    secret = "whsec_test"
    body = b'{"event":"invoice.issued"}'
    ts = 1758000000
    sig = wh.sign(secret, ts, body)
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_url_validation_blocks_metadata_and_https_only():
    assert "http" in (wh.validate_target_url("ftp://x.example.com/hook",
                                             allow_private=True) or "")
    assert wh.validate_target_url("http://169.254.169.254/latest", allow_private=True)
    assert wh.validate_target_url("http://127.0.0.1:9999/hook", allow_private=True) is None
    assert wh.validate_target_url("http://127.0.0.1:9999/hook", allow_private=False)
    assert wh.validate_target_url("https://example.com/hooks/x", allow_private=False) is None


# ---------------- assistant over API ----------------

@pytest.mark.asyncio
async def test_assistant_requires_permission(client, migrated_db):
    r = await client.post("/api/v1/assistant/ask", json={"question": "why did acme change?"})
    assert r.status_code == 401  # unauthenticated never reaches the tool router


@pytest.mark.asyncio
async def test_assistant_answers_with_citations_and_audit(client, migrated_db):
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)

    r = await client.get("/api/v1/assistant/capabilities", headers=_h(csrf))
    assert r.status_code == 200
    caps = r.json()
    assert caps["mode"] == "deterministic_demo" and caps["model_backed"] is False
    assert caps["can_write"] is False

    r = await client.post("/api/v1/assistant/ask",
                          json={"question": "Which credits have not been allocated?"},
                          headers=_h(csrf))
    assert r.status_code == 200, r.text
    a = r.json()
    if a["intent"] == "unallocated_credits" and not a["refused"] and not a["facts"]:
        # honest empty answer → nothing to cite; plant a credit and re-ask
        r = await client.post("/api/v1/credits", json={
            "display_name": f"P5 unallocated {uuid.uuid4().hex[:6]}",
            "kind": "promotional", "amount_total": "120.00"}, headers=_h(csrf))
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/assistant/ask",
                              json={"question": "Which credits have not been allocated?"},
                              headers=_h(csrf))
        a = r.json()
    assert not a["refused"] and a["intent"] == "unallocated_credits"
    assert a["facts"], "planted credit must appear"
    assert a["citations"], "every answered query must cite records"
    assert all("type" in c for c in a["citations"])

    # refusal is honest, not invented
    r = await client.post("/api/v1/assistant/ask",
                          json={"question": "what is the weather in paris"},
                          headers=_h(csrf))
    a2 = r.json()
    assert a2["refused"] and a2["refusal_reason"]

    # audit trail contains both, marked answered/refused
    r = await client.get("/api/v1/assistant/audit?page_size=20", headers=_h(csrf))
    assert r.status_code == 200
    rows = r.json()["items"]
    assert any(x["intent"] == "unallocated_credits" and not x["refused"] for x in rows)
    assert any(x["refused"] for x in rows)
    assert all(x["mode"] == "deterministic_demo" for x in rows)


@pytest.mark.asyncio
async def test_assistant_customer_safe_boundary(client, migrated_db):
    """Customer roles get answers computed within their own scope; partner-only
    intents are refused with a reason (never a leak, never a fake)."""
    csrf = await _login_csrf(client, "admin@acme-cloud.example.com")
    r = await client.post("/api/v1/assistant/ask",
                          json={"question": "Which customers are below target margin?"},
                          headers=_h(csrf))
    assert r.status_code == 200
    a = r.json()
    assert a["refused"] and "margin" in (a["refusal_reason"] or "").lower()

    r = await client.post("/api/v1/assistant/ask",
                          json={"question": "Why did Acme's invoice go up?"},
                          headers=_h(csrf))
    a2 = r.json()
    if not a2["refused"]:
        assert a2["intent"] in ("invoice_change", "draft_customer_safe")
        # customer answer must not expose provider cost/margin fields
        blob = str(a2["facts"]) + a2["text"]
        assert "provider_cost" not in blob and "margin_pct" not in blob


# ---------------- webhooks + ERP export over API ----------------

@pytest.mark.asyncio
async def test_webhook_lifecycle_and_secret_visibility(client, migrated_db):
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    url = f"http://127.0.0.1:9999/hooks/{uuid.uuid4().hex[:6]}"

    r = await client.post("/api/v1/integrations/webhooks",
                          json={"url": url, "events": ["invoice.issued", "integration.test"],
                                "description": "test target"}, headers=_h(csrf))
    assert r.status_code == 201, r.text
    created = r.json()
    secret = created["signing_secret"]
    assert secret.startswith("whsec_") and created["id"]

    # overview masks the secret
    r = await client.get("/api/v1/integrations/overview", headers=_h(csrf))
    mine = next(e for e in r.json()["webhook_endpoints"] if e["url"] == url)
    blob = str(r.json())
    assert secret not in blob and mine["secret_configured"]

    # bad event -> 422; metadata target -> 422
    r = await client.post("/api/v1/integrations/webhooks",
                          json={"url": url, "events": ["not.a.real.event"]}, headers=_h(csrf))
    assert r.status_code == 422
    r = await client.post("/api/v1/integrations/webhooks",
                          json={"url": "http://169.254.169.254/latest/meta-data",
                                "events": ["invoice.issued"]}, headers=_h(csrf))
    assert r.status_code == 422

    # test event queues a delivery and the sweep attempts it (nothing listens
    # on 9999 -> delivery ends failed/pending with an error, status visible)
    r = await client.post(f"/api/v1/integrations/webhooks/{created['id']}/test",
                          json={}, headers=_h(csrf))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] >= 1 and body["attempts_this_call"] >= 1
    assert body["latest"][0]["status"] in ("failed", "pending")

    r = await client.get(f"/api/v1/integrations/webhooks/{created['id']}/deliveries",
                         headers=_h(csrf))
    assert r.status_code == 200 and r.json()["total"] >= 1


@pytest.mark.asyncio
async def test_real_invoice_issue_queues_signed_delivery(client, migrated_db):
    """Full loop: create endpoint on a local loopback target, price + issue an
    invoice, and verify a delivery row was queued and actually delivered with a
    verifiable signature (receiver-side HMAC check)."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length", "0"))
            received["body"] = self.rfile.read(n)
            received["sig"] = self.headers.get("X-CPPartnerOps-Signature", "")
            received["ts"] = self.headers.get("X-CPPartnerOps-Timestamp", "")
            received["idem"] = self.headers.get("Idempotency-Key", "")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):  # silence
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
        await _ensure_demo_data(client, csrf)
        suffix = uuid.uuid4().hex[:6].upper()

        r = await client.post("/api/v1/integrations/webhooks", json={
            "url": f"http://127.0.0.1:{port}/hook",
            "events": ["invoice.issued"],
        }, headers=_h(csrf))
        assert r.status_code == 201
        created_r = r.json()
        secret = created_r["signing_secret"]

        # contract + 12% markup rule on ACME (same recipe as smoke_phase1)
        r = await client.get("/api/v1/customers?search=Acme", headers=_h(csrf))
        acme = next(i for i in r.json()["items"] if i["code"] == "ACME")
        r = await client.post("/api/v1/contracts", json={
            "customer_id": acme["id"], "code": f"P5{suffix}", "name": f"P5 {suffix}",
            "effective_start": "2026-06-01T00:00:00+00:00",
        }, headers=_h(csrf))
        assert r.status_code == 201, r.text
        cid, vid = r.json()["id"], r.json()["version_id"]
        r = await client.post("/api/v1/billing-rules", json={
            "contract_id": cid, "code": f"P5{suffix}M", "name": "markup",
            "rule_type": "percentage_markup"}, headers=_h(csrf))
        rid = r.json()["id"]
        r = await client.post(f"/api/v1/billing-rules/{rid}/versions", json={
            "parameters": {"type": "percentage_markup", "percent": "12"},
            "priority": 10, "calc_order": 10}, headers=_h(csrf))
        rv2 = r.json()["id"]
        await client.post(f"/api/v1/billing-rule-versions/{rv2}/publish", headers=_h(csrf))
        r = await client.patch(f"/api/v1/contract-versions/{vid}/bindings", json={
            "rule_bindings": [{"rule_id": rid, "rule_version_id": rv2, "order": 1}],
        }, headers=_h(csrf))
        assert r.status_code == 200
        # approve the rule version binding change if an approval gate exists
        await client.post(f"/api/v1/contract-versions/{vid}/activate", headers=_h(csrf))
        r = await client.post("/api/v1/pricing/runs", json={
            "customer_id": acme["id"], "contract_version_id": vid,
            "period_start": "2026-06-01T00:00:00+00:00",
            "period_end": "2026-07-01T00:00:00+00:00"}, headers=_h(csrf))
        assert r.status_code == 202, r.text
        run = r.json()
        r = await client.post("/api/v1/invoices", json={"run_id": run["run_id"]}, headers=_h(csrf))
        assert r.status_code == 201, r.text
        inv = r.json()
        for st in ("under_review", "approved", "issued"):
            r = await client.post(f"/api/v1/invoices/{inv['id']}/transition",
                                  json={"to_status": st}, headers=_h(csrf))
            if r.status_code != 200:
                # maker-checker: approve via approvals queue (Phase 2 flow)
                r2 = await client.get("/api/v1/approvals?status=pending", headers=_h(csrf))
                for ap in r2.json()["items"]:
                    await client.post(f"/api/v1/approvals/{ap['id']}/decide?decision=approved",
                                      headers=_h(csrf))
                r = await client.post(f"/api/v1/invoices/{inv['id']}/transition",
                                      json={"to_status": st}, headers=_h(csrf))
            assert r.status_code == 200, f"{st}: {r.text}"

        # delivery must have reached the listener with a valid signature
        from app.core.db import SessionLocal
        from app.services.webhooks import deliver_due
        async with SessionLocal() as s:
            await deliver_due(s)          # the beat sweep, run once
        r = await client.get(f"/api/v1/integrations/webhooks/{created_r['id']}/deliveries",
                             headers=_h(csrf))
        assert r.status_code == 200 and r.json()["total"] >= 1
        assert any(d["event"] == "invoice.issued" and d["status"] == "sent"
                   for d in r.json()["items"]), r.json()
        assert received.get("body"), "no HTTP POST reached the endpoint"
        import json as _json
        payload = _json.loads(received["body"])
        assert payload["event"] == "invoice.issued"
        assert payload["data"]["invoice_number"] == inv["invoice_number"]
        expect = "sha256=" + hmac.new(secret.encode(),
                                      f"{received['ts']}.".encode() + received["body"],
                                      hashlib.sha256).hexdigest()
        assert received["sig"] == expect, "signature must verify receiver-side"
        assert received["idem"]  # idempotency key present
    finally:
        srv.shutdown()


@pytest.mark.asyncio
async def test_erp_export_contains_no_partner_fields(client, migrated_db):
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)
    r = await client.get("/api/v1/invoices?page_size=1", headers=_h(csrf))
    items = r.json()["items"]
    assert items, "demo data should include at least one invoice"
    inv_id = items[0]["id"]

    r = await client.get(f"/api/v1/invoices/{inv_id}/export?fmt=json", headers=_h(csrf))
    assert r.status_code == 200, r.text
    blob = r.text
    doc = r.json()
    assert doc["schema"] == "cpo.erp.v1"
    assert Decimal(doc["totals"]["grand_total"]) >= 0
    for banned in ("provider_cost", "margin", "internal_note"):
        assert banned not in blob.lower()

    r = await client.get(f"/api/v1/invoices/{inv_id}/export?fmt=csv", headers=_h(csrf))
    assert r.status_code == 200 and "section,key,field,value" in r.text

    # every export is audited
    r = await client.get("/api/v1/audit-events?page_size=50", headers=_h(csrf))
    assert any(e["action"] == "export.generated" for e in r.json()["items"])


@pytest.mark.asyncio
async def test_scoped_api_token_auth(client, migrated_db):
    """Token creation (shown once), Bearer auth, scope intersection: a
    cost.read-only token can read cost-scoped data but is refused anything
    requiring a permission it does not carry."""
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    await _ensure_demo_data(client, csrf)
    me = (await client.get("/api/v1/auth/me", headers=_h(csrf))).json()
    r = await client.post("/api/v1/admin/tokens", json={
        "name": f"p5-{uuid.uuid4().hex[:6]}", "org_id": me["org_id"],
        "scopes": ["cost.read"],
    }, headers=_h(csrf))
    assert r.status_code == 201, r.text
    raw = r.json()["token"]
    assert raw.startswith("cpo_")

    # cookie-less client: Bearer is the only credential
    from httpx import ASGITransport, AsyncClient

    from app.main import app as fastapi_app
    async with AsyncClient(transport=ASGITransport(app=fastapi_app),
                           base_url="http://test") as bare:
        h = {"Authorization": f"Bearer {raw}"}
        r = await bare.get("/api/v1/finops/unit-economics", headers=h)
        assert r.status_code == 200, r.text  # holds cost.read
        r = await bare.get("/api/v1/margins/summary", headers=h)
        assert r.status_code == 403  # needs margin.view — token doesn't carry it
        r = await bare.get("/api/v1/customers")
        assert r.status_code == 401  # no cookie, no bearer → unauthenticated


@pytest.mark.asyncio
async def test_integration_record_reports_unconnected(client, migrated_db):
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    r = await client.post("/api/v1/integrations", json={
        "kind": "erp", "name": f"ERP {uuid.uuid4().hex[:6]}", "config": {"system": "demo"}},
        headers=_h(csrf))
    assert r.status_code == 201
    body = r.json()
    assert body["connected"] is False and "transport" in body["note"]
    iid = body["id"]
    r = await client.post(f"/api/v1/integrations/{iid}/check", headers=_h(csrf))
    assert r.status_code == 200 and r.json()["connected"] is False  # honest, not faked
