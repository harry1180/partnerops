"""Platform administration: role/permission catalog, health of jobs, token
management (scoped API tokens with rotation)."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CSRF, Principal, SessionDep
from app.core.config import get_settings
from app.db.rls import set_org_scope
from app.models.auth import ApiToken, Role, User, UserRoleAssignment
from app.services.audit_service import record_audit
from app.services.auth_service import hash_token, now_utc
from app.services.authz import PERMISSIONS, ROLE_PERMISSIONS

router = APIRouter()
settings = get_settings()


class CatalogOut(BaseModel):
    permissions: dict[str, str]
    roles: dict[str, list[str]]


@router.get("/demo-accounts")
async def demo_accounts():
    """Local demo only: returns seeded accounts so the login screen can list them.
    Disabled for non-local environments."""
    if not settings.is_local:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    from sqlalchemy import select

    from app.core.db import SessionLocal

    out = []
    async with SessionLocal() as s:
        rows = await s.execute(
            select(User.email, Role.key)
            .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .order_by(User.email)
        )
        for email, rk in rows.all():
            out.append({"email": email, "role": rk})
    return {
        "password_note": "All demo accounts share the SEED_DEMO_PASSWORD value (local only)",
        "accounts": out,
    }


@router.get("/catalog", response_model=CatalogOut)
async def catalog(principal: Principal):
    if not principal.can("user.manage") and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    return CatalogOut(permissions=PERMISSIONS, roles=ROLE_PERMISSIONS)


class TokenCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    org_id: uuid.UUID
    scopes: list[str] = Field(min_length=1)
    expires_days: int | None = Field(default=365, ge=1, le=730)


class TokenCreatedOut(BaseModel):
    id: uuid.UUID
    name: str
    token: str  # shown once
    scopes: list[str]
    expires_at: str | None


@router.post("/tokens", response_model=TokenCreatedOut, status_code=201, dependencies=[CSRF])
async def create_api_token(body: TokenCreate, session: SessionDep, principal: Principal):
    if not principal.can("api_token.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    from app.models.org import Organization

    org = await session.get(Organization, body.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    if not principal.is_platform_admin and not org.path.startswith(principal.scope_prefixes[0]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    for scope in body.scopes:
        if scope not in PERMISSIONS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "unknown_scope", "scope": scope})
        if not principal.can(scope):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "cannot_grant_unheld_permission", "scope": scope},
            )
    raw = "cpo_" + secrets.token_urlsafe(40)
    token = ApiToken(
        name=body.name,
        token_hash=hash_token(raw),
        org_id=body.org_id,
        created_by=principal.user_id,  # type: ignore[arg-type]
        scopes=sorted(body.scopes),
        expires_at=now_utc() + timedelta(days=body.expires_days) if body.expires_days else None,
    )
    await set_org_scope(session, org.path)
    session.add(token)
    await record_audit(
        session, principal, action="integration.changed", org_path=org.path,
        summary=f"API token '{body.name}' created (scopes: {sorted(body.scopes)})",
        entity_type="api_token", entity_id=token.id,
        detail={"scopes": body.scopes, "token": raw[:10] + "…"},
    )
    await session.commit()
    return TokenCreatedOut(
        id=token.id, name=token.name, token=raw, scopes=token.scopes,
        expires_at=token.expires_at.isoformat() if token.expires_at else None,
    )


@router.get("/tokens", response_model=list[dict], dependencies=[])
async def list_api_tokens(session: SessionDep, principal: Principal):
    from sqlalchemy import select

    if not principal.can("api_token.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    stmt = select(ApiToken).where(ApiToken.deleted_at.is_(None), ApiToken.revoked_at.is_(None))
    if not principal.is_platform_admin:
        stmt = stmt.where(ApiToken.org_id == principal.org_id)
    tokens = (await session.execute(stmt.order_by(ApiToken.created_at.desc()))).scalars()
    return [
        {
            "id": str(t.id), "name": t.name, "scopes": t.scopes,
            "created_at": t.created_at.isoformat(),
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        }
        for t in tokens
    ]


@router.post("/tokens/{token_id}/rotate", response_model=dict, dependencies=[CSRF])
async def rotate_api_token(token_id: uuid.UUID, session: SessionDep, principal: Principal):
    if not principal.can("api_token.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    old = await session.get(ApiToken, token_id)
    if old is None or old.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    if not principal.is_platform_admin and str(old.org_id) != str(principal.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    raw = "cpo_" + secrets.token_urlsafe(40)
    old.revoked_at = now_utc()
    new = ApiToken(
        name=old.name, token_hash=hash_token(raw), org_id=old.org_id,
        created_by=principal.user_id, scopes=old.scopes, expires_at=old.expires_at,  # type: ignore[arg-type]
    )
    session.add(new)
    await record_audit(
        session, principal, action="integration.changed",
        org_path=principal.org_path, summary=f"API token '{old.name}' rotated",
        entity_type="api_token", entity_id=new.id,
    )
    await session.commit()
    return {"id": str(new.id), "token": raw}
