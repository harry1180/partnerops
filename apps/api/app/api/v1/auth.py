"""Auth endpoints: login, logout, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import Principal, SessionDep, get_principal, verify_csrf
from app.core.config import get_settings
from app.core.state import STATE
from app.models.auth import Session as DbSession
from app.models.auth import User
from app.models.org import Organization
from app.services import auth_service, rate_limit
from app.services.audit_service import record_audit
from app.services.authz import LOCAL_IDP, RequestPrincipal

router = APIRouter()
settings = get_settings()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class CurrentUserOut(BaseModel):
    id: str
    email: str
    display_name: str
    org_id: str
    org_kind: str
    roles: list[str]
    permissions: list[str]
    scope_org_prefixes: list[str]
    mfa_enabled: bool


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _principal_out(user: User, principal: RequestPrincipal) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "org_id": str(principal.org_id),
        "org_kind": principal.org_kind,
        "roles": sorted(principal.roles),
        "permissions": sorted(principal.permissions),
        "scope_org_prefixes": list(principal.scope_prefixes),
        "mfa_enabled": user.mfa_enabled,
    }


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    session: SessionDep,
):
    ip = _client_ip(request)
    allowed, retry_after = await rate_limit.allow_login_attempt(
        STATE.get("redis"), f"{ip}|{body.email.lower()}"
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "retry_after_s": retry_after},
        )

    user = await LOCAL_IDP.authenticate(session, {"email": body.email, "password": body.password})
    if user is None:
        await record_audit(
            session,
            None,
            action="login.failure",
            org_path="/",
            summary=f"Failed login for {body.email.lower()}",
            detail={"email": body.email.lower()},
            correlation_id=getattr(request.state, "correlation_id", None),
            ip_address=ip,
            actor_kind="anonymous",
        )
        await session.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_credentials"},
            headers={"Retry-After": "60"},
        )

    assert isinstance(user, User)
    token, csrf = await auth_service.create_session(
        session, user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    org = await session.get(Organization, user.home_org_id)
    await record_audit(
        session,
        None,
        action="login.success",
        org_path=org.path if org else "/",
        summary=f"Login {user.email}",
        correlation_id=getattr(request.state, "correlation_id", None),
        ip_address=ip,
        actor_kind="user",
    )
    await session.commit()

    secure = not settings.is_local
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        httponly=False,  # readable by the SPA by design (double-submit pattern)
        secure=secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )
    principal = await auth_service.load_principal(session, user.id)
    assert principal is not None
    return {"ok": True, "csrf_token": csrf, "user": _principal_out(user, principal)}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    principal: Principal,
    _: None = Depends(verify_csrf),
):
    session_id = getattr(request.state, "session_id", None)
    if session_id:
        db_session = await session.get(DbSession, session_id)
        if db_session:
            await auth_service.revoke_session(session, db_session)
    await record_audit(
        session,
        principal,
        action="logout",
        org_path=principal.org_path,
        summary=f"Logout {principal.email}",
        correlation_id=getattr(request.state, "correlation_id", None),
    )
    await session.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return {"ok": True}



@router.get("/me", response_model=CurrentUserOut)
async def me(
    session: SessionDep,
    principal: Principal,
    _forced: RequestPrincipal = Depends(get_principal),  # ensures RLS binding
):
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "unauthenticated"})
    return _principal_out(user, principal)  # type: ignore[return-value]
