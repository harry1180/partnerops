"""Live HTTP smoke for Phase 7 (budget alerts + orphan event; API on :8001).

Journey: endpoint subscribed to budget.over_threshold + orphan.discovered
-> alert pass on the demo Cobalt cap opens episode #1 (delivered signed to a
REAL listener) -> re-run sends nothing (episode state) -> budgets feed shows
alert_state -> orphan.discovered exists for the unmapped AWS account ->
audit shows the pass. Re-runnable: unique budget per run, episodes are
per-budget state.

Run:  .venv/Scripts/python.exe tools/smoke_phase7_live.py [base_url]
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
steps: list[str] = []


def step(msg: str) -> None:
    steps.append(msg)
    print(f"  ok: {msg}")


def _password() -> str:
    from app.core.config import get_settings

    return get_settings().seed_demo_password


def _csrf(client: httpx.AsyncClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("cpo_csrf") or ""}


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
        received.append({"payload": json.loads(body),
                         "sig_ok": hmac.compare_digest(sig, expect)})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


async def main() -> int:
    tag = uuid.uuid4().hex[:6].upper()
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
        step("demo data ready")

        r = await c.post("/api/v1/integrations/webhooks", json={
            "url": f"http://127.0.0.1:{port}/hook",
            "events": ["budget.over_threshold", "orphan.discovered"],
            "description": f"P7 smoke {tag}"}, headers=h)
        assert r.status_code == 201, r.text
        ep_id, secret = r.json()["id"], r.json()["signing_secret"]
        _H.secret = secret
        step("endpoint subscribed to alert + orphan events")

        # a unique breaching budget in the ACTIVE window: August ACME spend
        # (the 14x-class fixture months) is well above 100
        r = await c.get("/api/v1/customers?search=Acme", headers=h)
        acme = next(i for i in r.json()["items"] if i["code"] == "ACME")
        r = await c.post("/api/v1/budgets", json={
            "name": f"P7 smoke {tag}", "amount": "100.00",
            "period_start": "2026-08-01T00:00:00+00:00",
            "period_end": "2026-09-01T00:00:00+00:00",
            "customer_id": acme["id"], "alert_threshold_pct": 80}, headers=h)
        assert r.status_code == 201, r.text
        bid = r.json()["id"]
        step("breaching budget created")

        # first pass: opens episode, delivers signed
        r = await c.post("/api/v1/budgets/evaluate-alerts", json={}, headers=h)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["opened"] >= 1
        step(f"alert pass: {res['evaluated']} evaluated, {res['opened']} opened")
        r = await c.post(f"/api/v1/integrations/webhooks/deliver?endpoint_id={ep_id}",
                         headers=h)
        assert r.json()["attempted"] >= 1
        await asyncio.sleep(0.4)
        got = [x for x in received
               if x["payload"].get("event") == "budget.over_threshold"
               and x["payload"]["data"]["budget_id"] == bid]
        assert got and got[-1]["sig_ok"], "alert event must arrive signed & verifiable"
        assert got[-1]["payload"]["data"]["episode"] == 1
        step("budget.over_threshold delivered with verified HMAC (episode #1)")

        # re-run: no duplicate
        r = await c.post("/api/v1/budgets/evaluate-alerts", json={}, headers=h)
        res2 = r.json()
        assert res2["opened"] == 0 and res2["still_open"] >= 1
        step("re-run sends zero duplicates (episode stays open)")

        # feed shows episode state
        r = await c.get("/api/v1/budgets", headers=h)
        mine = next(b for b in r.json()["items"] if b["id"] == bid)
        assert mine["alert_state"]["breached"] and mine["alert_state"]["alert_count"] == 1
        step("budgets feed carries alert_state (breached, episode #1)")

        # orphan.discovered coverage lives in pytest (a fresh synthetic org);
        # here assert the subscription is real and the event is in the catalog
        # (a future ingest with unmapped accounts lands on this endpoint).
        r = await c.get("/api/v1/integrations/overview", headers=h)
        assert "orphan.discovered" in r.json()["event_types"]
        mine_ep = next(e for e in r.json()["webhook_endpoints"] if e["id"] == ep_id)
        assert "orphan.discovered" in mine_ep["events"]
        step("orphan.discovered in event catalog + endpoint subscribed")

        # emails: partner alert roles + customer admins; the first sweep call
        # already flushed them through the local transport — verify the file
        r = await c.post("/api/v1/integrations/webhooks/deliver", headers=h)
        assert r.status_code == 200
        from pathlib import Path
        note_file = Path(__file__).resolve().parents[3] / "logs" / "notifications.ndjson"
        lines = [json.loads(x) for x in
                 note_file.read_text(encoding="utf-8").splitlines() if x.strip()] \
            if note_file.exists() else []
        alerts_sent = [x for x in lines if x.get("kind") == "budget_alert"]
        assert alerts_sent, "budget alert emails must reach the local transport"
        assert "msp@northwind-msp.example.com" in {x["to"] for x in alerts_sent}
        assert "billing@northwind-msp.example.com" not in {x["to"] for x in alerts_sent}
        step(f"budget alert emails flushed via local transport ({len(alerts_sent)} rows)")

        # audit trail
        r = await c.get("/api/v1/audit-events", params={"action": "budget.alerts_evaluated",
                                                        "page_size": 5}, headers=h)
        assert r.json()["items"], "alert pass must be audited"
        step("budget.alerts_evaluated in audit trail")

        # cleanup: soft-delete the smoke budget (episodes of deleted budgets
        # drop out of the active window)
        r = await c.delete(f"/api/v1/budgets/{bid}", headers=h)
        assert r.status_code == 200
        step("smoke budget deleted (re-runnable)")

    srv.shutdown()
    print(f"\nSMOKE7_OK — {len(steps)} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
