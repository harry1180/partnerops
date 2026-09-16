"""Authentication service: local login, sessions, rate limiting, lockout.

Sessions: 32-byte opaque token → cookie (HttpOnly, SameSite=Lax, Secure in
prod); server stores only its SHA-256 hash. CSRF uses double-submit: a
separate readable cookie whose value must be echoed in an X-CSRF-Token header
on unsafe methods and matched against the session's csrf_token.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.rls import set_org_scope
from app.models.audit import AuditEvent
from app.models.auth import Role, User, UserRoleAssignment
from app.models.auth import Session as DbSession
from app.models.org import Organization
from app.services.authz import RequestPrincipal, expand_role_permissions
from app.services.passwords import verify_password

log = get_logger(__name__)

LOCKOUT_THRESHOLD = 8
LOCKOUT_MINUTES = 15


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now_utc() -> datetime:
    return datetime.now(UTC)


async def record_login_audit(
    session: AsyncSession,
    *,
    user_id: object | None,
    org_path: str,
    action: str,
    summary: str,
    detail: dict | None,
    ip: str | None,
    correlation_id: str | None,
) -> None:
    """Login/audit writes bypass normal RLS scope by explicitly setting the
    target org path — the audit table trusts the service layer here."""
    await set_org_scope(session, org_path)
    session.add(
        AuditEvent(
            actor_user_id=user_id,  # type: ignore[arg-type]
            actor_kind="user" if user_id else "anonymous",
            actor_label=str(user_id or "anonymous"),
            org_path=org_path,
            action=action,
            entity_type="user",
            entity_id=user_id,  # type: ignore[arg-type]
            summary=summary,
            detail=detail or {},
            correlation_id=correlation_id,
            ip_address=ip,
        )
    )


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email.lower().strip(), User.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def verify_local_credentials(session: AsyncSession, email: str, password: str) -> User | None:
    """Returns the user on success; None on bad credentials, disabled or locked.
    Side effects: failed-count bookkeeping (lockout) — committed by caller."""
    user = await get_user_by_email(session, email)
    if user is None or user.status != "active" or not user.password_hash:
        # constant-ish time: still hash a dummy to reduce user-enumeration timing signal
        verify_password(password, "$argon2id$v=19$m=65536,t=3,p=2$dummy$dummy")
        return None
    if user.locked_until and user.locked_until > now_utc():
        return None
    if verify_password(password, user.password_hash):
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now_utc()
        return user
    user.failed_login_count += 1
    if user.failed_login_count >= LOCKOUT_THRESHOLD:
        user.locked_until = now_utc() + timedelta(minutes=LOCKOUT_MINUTES)
        user.failed_login_count = 0
    return None


async def create_session(
    session: AsyncSession,
    user: User,
    *,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, str]:
    """Create a DB session; returns (opaque_token, csrf_token)."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = now_utc() + timedelta(minutes=get_settings().access_token_ttl_minutes)
    session.add(
        DbSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            ip_address=ip,
            user_agent=(user_agent or "")[:512] or None,
            expires_at=expires,
        )
    )
    return token, csrf


async def revoke_session(session: AsyncSession, db_session: DbSession) -> None:
    db_session.revoked_at = now_utc()


async def prune_expired_sessions(session: AsyncSession) -> int:
    stmt = (
        update(DbSession)
        .where(DbSession.revoked_at.is_(None), DbSession.expires_at < now_utc())
        .values(revoked_at=now_utc())
    )
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


async def load_principal(session: AsyncSession, user_id: uuid.UUID) -> RequestPrincipal | None:
    """Build the authorization principal for a user: roles, expanded
    permissions, and effective org scope (their home-org subtree;
    platform_admin sees root).

    NOTE: the organizations table is RLS-scoped, so we temporarily bind the
    transaction to the root prefix for this resolution step — the row read is
    always the caller's own org (by PK). Callers must apply the principal's
    real scope via deps.apply_rls_scope before any business query.
    """
    await set_org_scope(session, "/")
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        return None
    org = await session.get(Organization, user.home_org_id)
    if org is None:
        return None
    role_rows = await session.execute(
        select(Role.key)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
        .where(UserRoleAssignment.user_id == user.id)
    )
    roles = frozenset(r for (r,) in role_rows.all())
    permissions = frozenset(expand_role_permissions(set(roles)))
    is_platform = "platform_admin" in roles
    # org.path already ends with "/" — the subtree prefix is the path itself.
    scope_prefixes = (org.path,) if not is_platform else ("/",)
    principal = RequestPrincipal(
        user_id=user.id,
        email=user.email,
        org_id=org.id,
        org_path=org.path,
        org_kind=org.kind,
        roles=roles,
        permissions=permissions,
        scope_prefixes=scope_prefixes,
        is_platform_admin=is_platform,
    )
    return principal


async def session_count_active(session: AsyncSession) -> int:
    stmt = select(func.count(DbSession.id)).where(
        DbSession.revoked_at.is_(None), DbSession.expires_at >= now_utc()
    )
    return int((await session.execute(stmt)).scalar_one())
