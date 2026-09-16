"""Audit trail writes.

Every mutation route calls record_audit() in the same transaction as its
change. The audit table is append-only (UPDATE/DELETE revoked in migration +
trigger guard). Detail dicts are passed through redact() so secrets and
password material can never land in audit payloads.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_scope
from app.models.audit import AuditEvent
from app.services.authz import RequestPrincipal

_REDACT_KEYS = {
    "password",
    "password_hash",
    "secret",
    "secret_key",
    "token",
    "api_key",
    "csrf_token",
    "session_token",
    "mfa_secret",
    "access_key",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***redacted***" if k.lower() in _REDACT_KEYS or "password" in k.lower() else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


async def record_audit(
    session: AsyncSession,
    principal: RequestPrincipal | None,
    *,
    action: str,
    org_path: str,
    summary: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    detail: dict | None = None,
    correlation_id: str | None = None,
    ip_address: str | None = None,
    actor_kind: str = "user",
) -> AuditEvent:
    await set_org_scope(session, org_path)
    event = AuditEvent(
        actor_user_id=principal.user_id if principal and actor_kind == "user" else None,
        actor_kind=actor_kind,
        actor_label=principal.email if principal else actor_kind,
        org_path=org_path,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        detail=redact(detail or {}),
        correlation_id=correlation_id,
        ip_address=ip_address,
    )
    session.add(event)
    return event
