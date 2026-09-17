"""Branding endpoints.

GET /branding/current — public-ish: returns the branding visible to the
caller's subtree (login screen, console chrome, invoice/report headers).
PUT /branding — updates within scope (branding.manage). Logo uploads go
through object storage with file-type validation (PNG/JPEG/SVG-WARNING:
SVG served with Content-Disposition + CSP; we accept PNG/JPEG only).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.models.branding import BrandingConfig
from app.services.audit_service import record_audit
from app.services.storage import get_storage

router = APIRouter()

_ALLOWED_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
_MAX_LOGO_BYTES = 2 * 1024 * 1024


class BrandingOut(BaseModel):
    product_name: str
    logo_url: str | None
    primary_color: str
    accent_color: str
    support_email: str | None
    email_sender_name: str | None
    custom_domain: str | None
    terminology: dict
    feature_flags: dict
    org_path: str


class BrandingUpdate(BaseModel):
    product_name: str | None = Field(default=None, min_length=2, max_length=128)
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    support_email: str | None = None
    email_sender_name: str | None = None
    custom_domain: str | None = None
    terminology: dict | None = None
    feature_flags: dict | None = None
    invoice_branding: dict | None = None


async def _resolve_branding(session: SessionDep, org_path: str) -> BrandingConfig | None:
    """Nearest ancestor branding wins; fall back to platform root config."""
    segments = [s for s in org_path.split("/") if s]
    for depth in range(len(segments), -1, -1):
        prefix = "/" + "/".join(segments[:depth]) + "/" if depth else "/"
        cfg = (
            await session.execute(
                select(BrandingConfig)
                .where(BrandingConfig.org_path == prefix, BrandingConfig.deleted_at.is_(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        if cfg:
            return cfg
    return None


def _to_out(cfg: BrandingConfig) -> BrandingOut:
    logo = f"/api/v1/branding/logo/{cfg.id}" if cfg.logo_object_key else None
    return BrandingOut(
        product_name=cfg.product_name,
        logo_url=logo,
        primary_color=cfg.primary_color,
        accent_color=cfg.accent_color,
        support_email=cfg.support_email,
        email_sender_name=cfg.email_sender_name,
        custom_domain=cfg.custom_domain,
        terminology=cfg.terminology,
        feature_flags=cfg.feature_flags,
        org_path=cfg.org_path,
    )


@router.get("/current", response_model=BrandingOut)
async def current_branding(session: SessionDep, principal: Principal):
    cfg = await _resolve_branding(session, principal.org_path)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "no_branding"})
    return _to_out(cfg)


@router.get("/public")
async def public_branding():
    """Unauthenticated: used by the login screen. Reads only the platform-root
    branding row (never tenant-scoped data for unknown visitors)."""
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        await set_org_scope(session, "/")
        cfg = (
            await session.execute(
                select(BrandingConfig).where(BrandingConfig.org_path == "/").limit(1)
            )
        ).scalar_one_or_none()
        if cfg is None:
            return {"product_name": "Cloud PartnerOps", "primary_color": "#0F3D5C", "accent_color": "#C9A227"}
        return {
            "product_name": cfg.product_name,
            "primary_color": cfg.primary_color,
            "accent_color": cfg.accent_color,
            "logo_present": bool(cfg.logo_object_key),
        }


@router.put("", response_model=BrandingOut, dependencies=[CSRF])
async def update_branding(body: BrandingUpdate, session: SessionDep, principal: Principal):
    if not principal.can("branding.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    await set_org_scope(session, principal.scope_prefixes[0])
    cfg = (
        await session.execute(
            select(BrandingConfig)
            .where(BrandingConfig.org_path == principal.org_path, BrandingConfig.deleted_at.is_(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if cfg is None:
        if principal.org_path not in principal.scope_prefixes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "outside_scope"})
        cfg = BrandingConfig(
            org_id=principal.org_id,
            org_path=principal.org_path,
            product_name="Cloud PartnerOps",
        )
        session.add(cfg)
        await session.flush()
    changed = {}
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(cfg, k, v)
        changed[k] = v
    await record_audit(
        session, principal, action="branding.updated", org_path=principal.org_path,
        summary=f"Branding updated: {sorted(changed)}", entity_type="branding", entity_id=cfg.id,
        detail={"fields": sorted(changed)},
    )
    await session.commit()
    return _to_out(cfg)


@router.post("/logo", response_model=dict, dependencies=[CSRF])
async def upload_logo(
    session: SessionDep,
    principal: Principal,
    file: UploadFile = File(...),
):
    if not principal.can("branding.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    content = await file.read()
    if len(content) > _MAX_LOGO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail={"code": "too_large"})
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "unsupported_type", "allowed": sorted(_ALLOWED_IMAGE_TYPES)},
        )
    if content[:8] != b"\x89PNG\r\n\x1a\n" and content[:2] != b"\xff\xd8":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "content_type_mismatch"})
    ext = _ALLOWED_IMAGE_TYPES[file.content_type]
    key = f"branding/{principal.org_id}/logo-{uuid.uuid4().hex}{ext}"
    get_storage().put_bytes(key, content, content_type=file.content_type)
    await set_org_scope(session, principal.org_path)
    cfg = (
        await session.execute(
            select(BrandingConfig).where(BrandingConfig.org_path == principal.org_path).limit(1)
        )
    ).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "no_branding_config"})
    cfg.logo_object_key = key
    await record_audit(
        session, principal, action="branding.logo_updated", org_path=principal.org_path,
        summary="Logo updated", entity_type="branding", entity_id=cfg.id,
    )
    await session.commit()
    return {"ok": True, "logo_url": f"/api/v1/branding/logo/{cfg.id}"}


@router.get("/logo/{branding_id}")
async def get_logo(branding_id: uuid.UUID, session: SessionDep, principal: Principal):
    cfg = await session.get(BrandingConfig, branding_id)
    if cfg is None or not cfg.logo_object_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    if not cfg.org_path.startswith(principal.scope_prefixes[0]) and not principal.is_platform_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
    data = get_storage().get_bytes(cfg.logo_object_key)
    media = "image/png" if cfg.logo_object_key.endswith(".png") else "image/jpeg"
    from fastapi.responses import Response

    return Response(content=data, media_type=media, headers={"Cache-Control": "public, max-age=300"})
