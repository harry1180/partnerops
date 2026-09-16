"""Audit trail read API (auditor + admins). Append-only: there is no update
or delete route by design. Cross-tenant reads are limited by org_path
prefixes of the caller's granted scope."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import (
    Principal,
    SessionDep,
    verify_csrf,  # noqa: F401  (documents: writes never here)
)
from app.models.audit import AuditEvent

router = APIRouter()


class AuditEventOut(BaseModel):
    id: uuid.UUID
    created_at: str
    actor_kind: str
    actor_label: str
    action: str
    entity_type: str | None
    entity_id: str | None
    summary: str
    org_path: str
    correlation_id: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int
    page: int
    page_size: int


@router.get("", response_model=AuditPage)
async def list_audit_events(
    session: SessionDep,
    principal: Principal,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    if not principal.can("audit.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    stmt = select(AuditEvent)
    count_stmt = select(func.count(AuditEvent.id))
    if not principal.is_platform_admin:
        root = principal.scope_prefixes[0]
        stmt = stmt.where(AuditEvent.org_path.like(root + "%"))
        count_stmt = count_stmt.where(AuditEvent.org_path.like(root + "%"))
    if action:
        stmt = stmt.where(AuditEvent.action == action)
        count_stmt = count_stmt.where(AuditEvent.action == action)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
        count_stmt = count_stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
        count_stmt = count_stmt.where(AuditEvent.entity_id == entity_id)
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        await session.execute(
            stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    items = [
        AuditEventOut(
            id=r.id,
            created_at=r.created_at.isoformat(),
            actor_kind=r.actor_kind,
            actor_label=r.actor_label,
            action=r.action,
            entity_type=r.entity_type,
            entity_id=str(r.entity_id) if r.entity_id else None,
            summary=r.summary,
            org_path=r.org_path,
            correlation_id=r.correlation_id,
        )
        for r in rows
    ]
    return AuditPage(items=items, total=total, page=page, page_size=page_size)
