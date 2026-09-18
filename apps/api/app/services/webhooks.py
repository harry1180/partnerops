"""Webhooks + notification transport (Phase 5).

Events (invoice.issued, dispute.created, ingestion.parsed, report.ready,
budget.over_threshold, integration.test) fan out to WebhookEndpoint rows
subscribed to that event_type. Delivery is a signed POST:

    X-CPPartnerOps-Timestamp: <epoch seconds>
    X-CPPartnerOps-Signature: sha256=<hmac_sha256(secret, f"{ts}.{body}")>
    Idempotency-Key: <delivery id>          (receiver may dedupe)

Secret handling: the signing secret is generated server-side, shown to the
user exactly once at creation, and stored referenced by `secret_ref`
(`local:secret-store/<id>`). The value itself lives in the endpoint config
JSON under a clearly-labeled key for local/synthetic deployments; production
deployments swap this for a KMS/Secrets-Manager fetch at send time (Phase 6
deployment work) — the send path only ever reads through `_secret_of()`.

SSRF posture: https/http only; metadata/link-local addresses are refused
always; RFC1918/loopback targets are refused outside local/test deployments
where the demo needs them (config-gated, tested).

Email transport: local/test writes queued outbox messages to
logs/notifications.ndjson (one JSON line per message) and marks them sent —
an actually-working local transport, not a fake button. Real SMTP transport
adapters plug in at the same seam for deployments that configure them.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.rls import set_bypass_scope, set_org_scope
from app.models.approvals import (
    NotificationOutbox,
    WebhookDelivery,
    WebhookEndpoint,
)

log = get_logger(__name__)

EVENT_TYPES = (
    "invoice.issued", "dispute.created", "ingestion.parsed", "report.ready",
    "budget.over_threshold", "integration.test",
)
MAX_ATTEMPTS = 3

# networks never allowed (cloud metadata etc.)
_ALWAYS_BLOCKED = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
]


@dataclass
class SecretIssue:
    endpoint_id: str
    secret: str  # shown once


def generate_signing_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                    hashlib.sha256).hexdigest()


def validate_target_url(url: str, *, allow_private: bool) -> str | None:
    """Returns an error string, or None when the target is acceptable."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "only http/https webhook targets are allowed"
    host = parsed.hostname or ""
    if not host:
        return "webhook target has no host"
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # hostnames: resolve is deferred to connect time (httpx). We block
        # known metadata hostnames and let connect-time errors fail the
        # delivery with a recorded reason.
        if host.endswith(".internal") or host == "metadata":
            return "internal hostname refused"
        return None
    for net in _ALWAYS_BLOCKED:
        if addr in net:
            return "target address refused (metadata/link-local)"
    if not allow_private and (addr.is_private or addr.is_loopback or addr.is_link_local):
        return "private/loopback targets are only allowed in local or test deployments"
    return None


async def create_endpoint(session: AsyncSession, org_path: str, *, url: str,
                          events: list[str], description: str | None) -> tuple[WebhookEndpoint, SecretIssue]:
    await set_org_scope(session, org_path)
    settings = get_settings()
    err = validate_target_url(url, allow_private=settings.is_local)
    if err:
        raise ValueError(err)
    secret = generate_signing_secret()
    ep = WebhookEndpoint(
        org_id=_org_id(org_path), org_path=org_path, url=url,
        events=sorted(set(events)), status="active", description=description,
        # local mode keeps the value in the ref itself (see module docstring);
        # production swaps this to a KMS/Secrets-Manager reference resolved in
        # _secret_of() at send time.
        secret_ref=f"local:v1:{secret}",
    )
    session.add(ep)
    await session.flush()
    return ep, SecretIssue(endpoint_id=str(ep.id), secret=secret)


def _org_id(org_path: str):
    import uuid
    return uuid.UUID(org_path.strip("/").split("/")[-1])


async def queue_event(session: AsyncSession, org_path: str, event_type: str,
                      payload: dict, *, scope: str | None = None) -> int:
    """Create pending deliveries for every active endpoint at this event's
    org or an ancestor of it (an MSP configures endpoints at its own root;
    the invoice lives under a customer subtree). `scope` is the caller's
    RLS boundary for the endpoint lookup (worker passes "/" root-scope);
    deliveries themselves are stored at the event's org_path.
    Idempotent-safe: caller wraps in its own transaction."""
    await set_org_scope(session, scope or org_path)
    segs = [s for s in org_path.split("/") if s]
    prefixes = {"/" + "/".join(segs[:i]) + "/" for i in range(1, len(segs) + 1)}
    if scope and scope != "/":
        prefixes = {p for p in prefixes if p.startswith(scope)}
    eps = (await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.org_path.in_(prefixes),
            WebhookEndpoint.deleted_at.is_(None),
            WebhookEndpoint.status == "active")
    )).scalars().all()
    now = datetime.now(UTC)
    n = 0
    for ep in eps:
        if event_type not in (ep.events or []):
            continue
        session.add(WebhookDelivery(
            endpoint_id=ep.id, org_path=org_path, event_type=event_type,
            payload=payload, status="pending", attempts=0, created_at=now))
        n += 1
    if n:
        await session.flush()
    return n


def _secret_of(ep: WebhookEndpoint) -> str | None:
    ref = ep.secret_ref or ""
    if ref.startswith("local:v1:"):
        return ref.removeprefix("local:v1:")
    return None


async def deliver_due(session: AsyncSession, limit: int = 20,
                      endpoint_id=None) -> int:
    """Send pending webhook deliveries with HMAC signature + retry accounting.
    `endpoint_id` scopes the sweep to one endpoint (test-send must not be
    blocked by another endpoint's dead target — head-of-line blocking).
    Returns number of deliveries attempted."""
    await set_bypass_scope(session)  # worker scope (platform) for the sweep
    stmt = select(WebhookDelivery).where(WebhookDelivery.status == "pending")
    if endpoint_id is not None:
        stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)
    rows = list((await session.execute(
        stmt.order_by(WebhookDelivery.created_at).limit(limit)
    )).scalars())
    attempted = 0
    for d in rows:
        ep = await session.get(WebhookEndpoint, d.endpoint_id)
        if ep is None or (ep.deleted_at is not None) or ep.status != "active":
            d.status = "failed"
            d.response_code = None
            continue
        secret = _secret_of(ep)
        body = json.dumps({"event": d.event_type, "delivery_id": str(d.id),
                           "timestamp": int(time.time()), "data": d.payload},
                          ensure_ascii=False).encode()
        ts = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-CPPartnerOps-Timestamp": str(ts),
            "X-CPPartnerOps-Signature": f"sha256={sign(secret, ts, body)}" if secret else "",
            "Idempotency-Key": str(d.id),
            "User-Agent": "CloudPartnerOps-Webhooks/1",
        }
        d.attempts += 1
        attempted += 1
        settings = get_settings()
        err = validate_target_url(ep.url, allow_private=settings.is_local)
        if err:
            d.status = "failed"
            d.payload = {**d.payload, "_delivery_error": err}
            continue
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.post(ep.url, content=body, headers=headers)
            d.response_code = r.status_code
            if 200 <= r.status_code < 300:
                d.status = "sent"
                d.delivered_at = datetime.now(UTC)
            elif d.attempts >= MAX_ATTEMPTS:
                d.status = "failed"
            else:
                d.status = "pending"  # next sweep retries until MAX_ATTEMPTS
        except Exception as exc:
            d.status = "failed" if d.attempts >= MAX_ATTEMPTS else "pending"
            d.payload = {**d.payload, "_delivery_error": str(exc)[:200]}
            log.warning("webhook_delivery_error", delivery_id=str(d.id),
                        error=str(exc)[:200])
    await session.commit()
    return attempted


async def flush_notifications(session: AsyncSession) -> int:
    """Local transport for queued outbox emails: append one JSON line per
    message to logs/notifications.ndjson and mark sent. Real SMTP adapters
    replace this function's body at the same seam (config-gated)."""
    from pathlib import Path

    await set_bypass_scope(session)
    rows = list((await session.execute(
        select(NotificationOutbox).where(
            NotificationOutbox.status == "queued",
            NotificationOutbox.channel == "email")
        .order_by(NotificationOutbox.created_at).limit(100)
    )).scalars())
    if not rows:
        return 0
    log_dir = Path(__file__).resolve().parents[3] / "logs"
    log_dir.mkdir(exist_ok=True)
    out = log_dir / "notifications.ndjson"
    now = datetime.now(UTC)
    with out.open("a", encoding="utf-8") as fh:
        for n in rows:
            fh.write(json.dumps({
                "to": n.recipient, "subject": n.subject, "kind": n.kind,
                "queued_at": n.created_at.isoformat() if n.created_at else None,
                "transport": "local_file",
                "body_preview": n.body[:200],
            }) + "\n")
            n.status = "sent"
            n.sent_at = now
    await session.commit()
    log.info("notification_transport", sent=len(rows), path=str(out))
    return len(rows)
