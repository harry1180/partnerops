"""FastAPI dependencies: DB session with RLS scope, current principal,
permission gates, CSRF verification."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import correlation_id_var
from app.db.rls import set_bypass_scope, set_org_scope
from app.models.auth import ApiToken, User
from app.models.auth import Session as DbSession
from app.services.auth_service import hash_token, load_principal, now_utc
from app.services.authz import RequestPrincipal

settings = get_settings()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _read_session_token(request: Request) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


def _read_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[7:].strip()
        return token if token.startswith("cpo_") else None
    return None


async def _principal_from_session(request: Request, session: AsyncSession) -> RequestPrincipal | None:
    token = _read_session_token(request)
    if not token:
        return None
    stmt = select(DbSession).where(
        DbSession.token_hash == hash_token(token),
        DbSession.revoked_at.is_(None),
        DbSession.expires_at >= now_utc(),
    )
    db_session = (await session.execute(stmt)).scalar_one_or_none()
    if db_session is None:
        return None
    request.state.session_id = db_session.id
    request.state.csrf_token = db_session.csrf_token
    return await load_principal(session, db_session.user_id)


async def _principal_from_api_token(request: Request, session: AsyncSession) -> RequestPrincipal | None:
    token = _read_bearer(request)
    if not token:
        return None
    stmt = select(ApiToken).where(
        ApiToken.token_hash == hash_token(token),
        ApiToken.deleted_at.is_(None),
        ApiToken.revoked_at.is_(None),
    )
    api_token = (await session.execute(stmt)).scalar_one_or_none()
    if api_token is None:
        return None
    exp = api_token.expires_at
    if exp is not None and exp.tzinfo is None:  # SQLite returns naive timestamps
        exp = exp.replace(tzinfo=UTC)
    if exp is not None and exp < now_utc():
        return None
    user = await session.get(User, api_token.created_by) if api_token.created_by else None
    if user is None:
        return None
    principal = await load_principal(session, user.id)
    if principal is None:
        return None
    # token scopes intersect (never widen) the user's permissions
    if api_token.scopes:
        allowed = set(api_token.scopes)
        principal = RequestPrincipal(
            user_id=principal.user_id,
            email=principal.email,
            org_id=principal.org_id,
            org_path=principal.org_path,
            org_kind=principal.org_kind,
            roles=principal.roles,
            permissions=frozenset(p for p in principal.permissions if p in allowed),
            scope_prefixes=principal.scope_prefixes,
            is_platform_admin=False,
            attributes={**principal.attributes, "api_token_id": str(api_token.id)},
        )
    api_token.last_used_at = now_utc()
    request.state.api_token_id = api_token.id
    return principal


async def get_principal(
    request: Request,
    session: SessionDep,
) -> RequestPrincipal:
    """Resolve the caller (cookie session or API token) and bind the request's
    RLS scope to their primary org subtree. 401 when unauthenticated."""
    principal = await _principal_from_session(request, session)
    if principal is None:
        principal = await _principal_from_api_token(request, session)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "unauthenticated"})
    await apply_rls_scope(session, principal)
    request.state.principal = principal
    return principal


async def apply_rls_scope(session: AsyncSession, principal: RequestPrincipal) -> None:
    """Bind app.current_org_path to the caller's broadest allowed prefix.

    Defense-in-depth: even if a query forgets a WHERE clause, RLS drops rows
    outside the subtree. Cross-scope reads (auditors with multiple grants)
    are handled by re-applying the narrowest safe prefix per query — for the
    shipped single-subtree user model this is exact.
    """
    if principal.is_platform_admin:
        await set_bypass_scope(session)  # sets '/' — policies allow root subtree
    else:
        prefix = principal.scope_prefixes[0] if principal.scope_prefixes else ""
        await set_org_scope(session, prefix if prefix != "/" else None)


Principal = Annotated[RequestPrincipal, Depends(get_principal)]


def require(permission: str) -> Callable[[RequestPrincipal], RequestPrincipal]:
    def checker(principal: Principal) -> RequestPrincipal:
        if not principal.can(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "required": permission},
            )
        return principal

    return checker


def require_any(*permissions: str) -> Callable[[RequestPrincipal], RequestPrincipal]:
    def checker(principal: Principal) -> RequestPrincipal:
        if not any(principal.can(p) for p in permissions):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "required_any": list(permissions)},
            )
        return principal

    return checker


def verify_csrf(request: Request, principal: Principal) -> None:
    """Double-submit CSRF check for cookie-authenticated unsafe requests.
    API-token (Bearer) requests are not cookie-trusting → exempt.

    Depends on get_principal so FastAPI always resolves the session (and its
    csrf token) BEFORE this check runs — never fail-open.
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return
    if _read_bearer(request):
        return
    expected = getattr(request.state, "csrf_token", None)
    if expected is None:
        # session-authenticated but no session state → reject
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "csrf_failed"})
    supplied = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "csrf_failed"})


CSRF = Depends(verify_csrf)  # use as: dependencies=[CSRF]


def org_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_id"}) from exc


def correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "") or correlation_id_var.get()
