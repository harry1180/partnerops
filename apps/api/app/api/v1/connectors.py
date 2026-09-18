"""Connector API (Phase 3): multi-cloud ingestion scheduling surface.

CRUD + 'run now' for provider connectors, plus the status feed the Cloud
Accounts page renders. Live fetching is intentionally absent: mode is
'synthetic' and fetch_available is always false (ADR-0016) — the UI labels
this honestly instead of shipping a fake pull button.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CSRF, Principal, SessionDep
from app.services import connectors as svc

router = APIRouter()

CONNECTOR_KINDS = {"aws": "aws_cur", "azure": "azure_cost_export"}


class ConnectorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    provider_code: str = Field(pattern="^(aws|azure)$")
    billing_account_ref: str = Field(min_length=1, max_length=128)
    cadence: str = Field(default="monthly", pattern="^(monthly|weekly)$")
    day_of_month: int = Field(default=3, ge=1, le=28)
    hour_utc: int = Field(default=6, ge=0, le=23)


class ConnectorRunRequest(BaseModel):
    period: str = Field(min_length=7, max_length=7)  # YYYY-MM


@router.get("/connectors")
async def list_connectors(session: SessionDep, principal: Principal):
    if not principal.can("integration.manage"):
        raise HTTPException(403, detail={"code": "forbidden"})
    root = principal.scope_prefixes[0]
    return {"items": await svc.list_connectors(session, root)}


@router.post("/connectors", status_code=201, dependencies=[CSRF])
async def create_connector(body: ConnectorCreate, session: SessionDep, principal: Principal):
    if not principal.can("integration.manage"):
        raise HTTPException(403, detail={"code": "forbidden"})
    if principal.is_platform_admin:
        raise HTTPException(400, detail={"code": "platform_admin_must_scope"})
    try:
        c = await svc.create_connector(
            session, principal, name=body.name, provider_code=body.provider_code,
            connector_kind=CONNECTOR_KINDS[body.provider_code],
            billing_account_ref=body.billing_account_ref, cadence=body.cadence,
            day_of_month=body.day_of_month, hour_utc=body.hour_utc, config={},
        )
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "connector_exists", "message": str(exc)}) from None
    return {"id": str(c.id), "name": c.name, "next_due_at": c.next_due_at.isoformat()
            if c.next_due_at else None}


@router.post("/connectors/{cid}/toggle", dependencies=[CSRF])
async def toggle_connector(cid: uuid.UUID, enabled: bool, session: SessionDep,
                           principal: Principal):
    if not principal.can("integration.manage"):
        raise HTTPException(403, detail={"code": "forbidden"})
    try:
        c = await svc.toggle_connector(session, principal, cid, enabled)
    except LookupError:
        raise HTTPException(404, detail={"code": "not_found"}) from None
    return {"id": str(c.id), "enabled": c.enabled}


@router.post("/connectors/{cid}/run", dependencies=[CSRF])
async def run_connector(cid: uuid.UUID, body: ConnectorRunRequest, session: SessionDep,
                        principal: Principal):
    """Synthetic-mode run: ingest the fixture file for the requested period.
    There is no live-fetch path to run — see ADR-0016."""
    if not principal.can("integration.manage"):
        raise HTTPException(403, detail={"code": "forbidden"})
    try:
        result = await svc.run_connector_now(session, principal, cid, body.period)
    except LookupError:
        raise HTTPException(404, detail={"code": "not_found"}) from None
    return {
        "connector_id": str(result.connector_id), "status": result.status,
        "file_id": str(result.file_id) if result.file_id else None,
        "canonical": result.canonical, "detail": result.detail,
    }
