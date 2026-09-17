"""Approval (maker-checker) service helpers shared by all requesters."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approvals import ApprovalRequest


async def create_approval(
    session: AsyncSession, *, kind: str, entity_type: str, entity_id: uuid.UUID,
    summary: str, org_path: str, org_id: uuid.UUID, maker_id: uuid.UUID,
    impact_amount: Decimal | None = None, currency: str = "USD",
    context: dict | None = None,
) -> ApprovalRequest:
    n = int((await session.execute(select(func.count(ApprovalRequest.id)))).scalar_one())
    req = ApprovalRequest(
        request_number=f"APR-{n + 1:05d}", kind=kind, entity_type=entity_type,
        entity_id=entity_id, summary=summary, impact_amount=impact_amount,
        currency=currency, maker_id=maker_id, context=context or {},
        org_path=org_path, org_id=org_id,
    )
    session.add(req)
    await session.flush()
    return req


async def require_approved(
    session: AsyncSession, *, kind: str, entity_type: str, entity_id: uuid.UUID,
) -> ApprovalRequest | None:
    return (await session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.kind == kind,
            ApprovalRequest.entity_type == entity_type,
            ApprovalRequest.entity_id == entity_id,
            ApprovalRequest.status == "approved",
        ).limit(1)
    )).scalars().first()
