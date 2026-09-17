"""v1 API router aggregation.

Versioning strategy: /api/v1 is stable; breaking changes require /api/v2 with
a deprecation window (docs/api.md). Public demo endpoints are clearly labeled.
"""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    audit,
    auth,
    billing,
    branding,
    contracts,
    customers,
    extras,
    health_misc,
    margins,
    orgs,
    portal,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(orgs.router, prefix="/orgs", tags=["organizations"])
api_router.include_router(customers.router, prefix="/customers", tags=["customers"])
api_router.include_router(contracts.router, tags=["contracts"])
api_router.include_router(billing.router, tags=["billing"])
api_router.include_router(margins.router, tags=["margins"])
api_router.include_router(portal.router, tags=["portal"])
api_router.include_router(extras.router, tags=["billing-ops"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(branding.router, prefix="/branding", tags=["branding"])
api_router.include_router(audit.router, prefix="/audit-events", tags=["audit"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(health_misc.router, tags=["meta"])
