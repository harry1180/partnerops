"""User administration within scope."""

from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.auth import Role, User, UserRoleAssignment
from app.models.org import Organization
from app.services.audit_service import record_audit
from app.services.authz import ROLE_PERMISSIONS
from app.services.passwords import hash_password, validate_password_strength

router = APIRouter()


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=255)
    org_id: uuid.UUID
    roles: list[str] = Field(min_length=1)
    password: str | None = None


class RoleChange(BaseModel):
    roles: list[str] = Field(min_length=1)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    org_id: uuid.UUID
    status: str
    roles: list[str]
    last_login_at: str | None = None


def _in_scope(org: Organization | None, principal: Principal) -> bool:
    if org is None:
        return False
    if principal.is_platform_admin:
        return True
    if not principal.can("user.manage"):
        return False
    root = principal.scope_prefixes[0]
    return org.path.startswith(root)


@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, principal: Principal, org_id: uuid.UUID | None = None):
    if not principal.can("user.manage") and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    stmt = select(User).where(User.deleted_at.is_(None))
    if org_id:
        stmt = stmt.where(User.home_org_id == org_id)
    users = list((await session.execute(stmt.order_by(User.email))).scalars())
    role_rows = await session.execute(
        select(UserRoleAssignment.user_id, Role.key).join(Role, Role.id == UserRoleAssignment.role_id)
    )
    per_user: dict[uuid.UUID, list[str]] = {}
    for uid, key in role_rows.all():
        per_user.setdefault(uid, []).append(key)
    out = []
    for u in users:
        org = await session.get(Organization, u.home_org_id)
        if not _in_scope(org, principal):
            continue
        out.append(
            UserOut(
                id=u.id, email=u.email, display_name=u.display_name, org_id=u.home_org_id,
                status=u.status, roles=sorted(per_user.get(u.id, [])),
                last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            )
        )
    return out


@router.post("", response_model=UserOut, status_code=201, dependencies=[CSRF])
async def create_user(body: UserCreate, session: SessionDep, principal: Principal):
    if not principal.can("user.manage") and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    org = await session.get(Organization, body.org_id)
    if not _in_scope(org, principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    existing = (
        await session.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "email_exists"})
    for role_key in body.roles:
        if role_key not in ROLE_PERMISSIONS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "unknown_role", "role": role_key}
            )
    password_hash = None
    if body.password:
        problems = validate_password_strength(body.password)
        if problems:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail={"code": "weak_password", "problems": problems}
            )
        password_hash = hash_password(body.password)
    elif principal.is_platform_admin is False:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "password_required",
                "message": "Set an initial password (SSO-only users arrive with later identity providers)",
            },
        )
    user = User(
        email=body.email.lower(),
        display_name=body.display_name,
        home_org_id=body.org_id,
        password_hash=password_hash,
    )
    await set_org_scope(session, org.path if org else principal.org_path)
    session.add(user)
    await session.flush()
    for role_key in body.roles:
        role = (await session.execute(select(Role).where(Role.key == role_key))).scalar_one()
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, org_id=body.org_id))
    await record_audit(
        session, principal, action="user.created", org_path=org.path if org else principal.org_path,
        summary=f"Created user {user.email} with roles {sorted(body.roles)}",
        entity_type="user", entity_id=user.id, detail={"roles": body.roles},
    )
    await session.commit()
    return UserOut(
        id=user.id, email=user.email, display_name=user.display_name, org_id=user.home_org_id,
        status=user.status, roles=sorted(body.roles),
    )


@router.patch("/{user_id}/roles", response_model=dict, dependencies=[CSRF])
async def change_roles(user_id: uuid.UUID, body: RoleChange, session: SessionDep, principal: Principal):
    if not principal.can("user.manage") and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    org = await session.get(Organization, user.home_org_id)
    if not _in_scope(org, principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    for role_key in body.roles:
        if role_key not in ROLE_PERMISSIONS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "unknown_role"})
    if user.id == principal.user_id and "platform_admin" in body.roles and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "cannot_grant_self_platform_admin"})
    existing = await session.execute(
        select(UserRoleAssignment, Role.key)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(UserRoleAssignment.user_id == user.id)
    )
    current = {key: assign for assign, key in existing.all()}
    for key, assign in current.items():
        if key not in body.roles:
            await session.delete(assign)
    for key in body.roles:
        if key not in current:
            role = (await session.execute(select(Role).where(Role.key == key))).scalar_one()
            session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, org_id=user.home_org_id))
    await record_audit(
        session, principal, action="user.role_changed",
        org_path=org.path if org else principal.org_path,
        summary=f"Roles for {user.email}: {sorted(current)} → {sorted(body.roles)}",
        entity_type="user", entity_id=user.id,
        detail={"before": sorted(current), "after": sorted(body.roles)},
    )
    await session.commit()
    return {"ok": True}


@router.post("/{user_id}/disable", response_model=dict, dependencies=[CSRF])
async def disable_user(user_id: uuid.UUID, session: SessionDep, principal: Principal):
    if not principal.can("user.manage") and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    if user_id == principal.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "cannot_disable_self"})
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    org = await session.get(Organization, user.home_org_id)
    if not _in_scope(org, principal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    user.status = "disabled"
    from datetime import datetime

    from app.models.auth import Session as DbSession

    sessions = (
        await session.execute(
            select(DbSession).where(DbSession.user_id == user.id, DbSession.revoked_at.is_(None))
        )
    ).scalars()
    now = datetime.now(UTC)
    for s in sessions:
        s.revoked_at = now
    await record_audit(
        session, principal, action="user.disabled", org_path=org.path if org else principal.org_path,
        summary=f"Disabled user {user.email} (sessions revoked)", entity_type="user", entity_id=user.id,
    )
    await session.commit()
    return {"ok": True}
