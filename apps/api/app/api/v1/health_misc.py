"""Meta endpoints: capability + navigation manifest (branding-safe)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Principal
from app.core.config import get_settings
from app.services.authz import PERMISSIONS, ROLE_PERMISSIONS

router = APIRouter()
settings = get_settings()

# Deployment phase of the shipped code. The UI disables any nav entry whose
# available_from_phase exceeds this — a page may only link when it exists.
CURRENT_PHASE = 4

# Navigation manifest — the single source of truth for main-nav sections and
# which permission gates each. The UI renders ONLY sections whose permission
# the caller holds, so there are no dead links; entries with phase > 0 are
# labeled with their release phase by the UI from `available_from_phase`.
NAVIGATION: list[dict] = [
    {"key": "overview", "label": "Executive Overview", "href": "/overview", "permission": "customer.read", "available_from_phase": 0},
    {"key": "customers", "label": "Customers", "href": "/customers", "permission": "customer.read", "available_from_phase": 0},
    {"key": "cloud_accounts", "label": "Cloud Accounts", "href": "/cloud-accounts", "permission": "customer.read", "available_from_phase": 1},
    {"key": "contracts", "label": "Contracts", "href": "/contracts", "permission": "contract.read", "available_from_phase": 1},
    {"key": "billing_rules", "label": "Billing Rules", "href": "/billing-rules", "permission": "contract.read", "available_from_phase": 1},
    {"key": "commitments", "label": "Commitments", "href": "/commitments", "permission": "customer.read", "available_from_phase": 2},
    {"key": "credits", "label": "Credits & Discounts", "href": "/credits", "permission": "customer.read", "available_from_phase": 2},
    {"key": "pricing", "label": "Pricing Runs", "href": "/pricing", "permission": "pricing.run", "available_from_phase": 1},
    {"key": "usage", "label": "Usage Explorer", "href": "/usage", "permission": "cost.read", "available_from_phase": 1},
    {"key": "invoices", "label": "Invoices", "href": "/invoices", "permission": "invoice.read", "available_from_phase": 1},
    {"key": "reconciliation", "label": "Reconciliation", "href": "/reconciliation", "permission": "recon.read", "available_from_phase": 1},
    {"key": "margins", "label": "Margins", "href": "/margins", "permission": "margin.view", "available_from_phase": 1},
    {"key": "budgets", "label": "Budgets & Anomalies", "href": "/budgets", "permission": "customer.read", "available_from_phase": 4},
    {"key": "optimization", "label": "Optimization", "href": "/optimization", "permission": "customer.read", "available_from_phase": 4},
    {"key": "governance", "label": "Governance", "href": "/governance", "permission": "customer.read", "available_from_phase": 4},
    {"key": "reports", "label": "Reports", "href": "/reports", "permission": "report.read", "available_from_phase": 2}
    ,{"key": "approvals", "label": "Approvals", "href": "/approvals", "permission": "contract.approve", "available_from_phase": 2},
    {"key": "integrations", "label": "Integrations", "href": "/integrations", "permission": "integration.manage", "available_from_phase": 5},
    {"key": "audit", "label": "Audit Trail", "href": "/audit", "permission": "audit.read", "available_from_phase": 0},
    {"key": "administration", "label": "Administration", "href": "/admin", "permission": "user.manage", "available_from_phase": 0},
]


PORTAL_NAVIGATION: list[dict] = [
    {"key": "portal_overview", "label": "Cost Overview", "href": "/portal", "permission": "customer.read", "available_from_phase": 1},
    {"key": "portal_usage", "label": "Usage Explorer", "href": "/portal/usage", "permission": "cost.read", "available_from_phase": 1},
    {"key": "portal_invoices", "label": "Invoices", "href": "/portal/invoices", "permission": "invoice.read", "available_from_phase": 1},
    {"key": "portal_budgets", "label": "Budgets", "href": "/portal/budgets", "permission": "customer.read", "available_from_phase": 4},
    {"key": "portal_anomalies", "label": "Cost Changes", "href": "/portal/anomalies", "permission": "customer.read", "available_from_phase": 4},
]


@router.get("/capabilities")
async def capabilities(principal: Principal):
    nav = PORTAL_NAVIGATION if principal.org_kind == "customer" else NAVIGATION
    return {
        "product_version": settings.version,
        "current_phase": CURRENT_PHASE,
        "roles": sorted(principal.roles),
        "permissions": sorted(principal.permissions),
        "navigation": [
            {**entry, "granted": principal.can(entry["permission"])} for entry in nav
        ],
        "role_catalog_keys": list(ROLE_PERMISSIONS.keys()),
        "permission_count": len(PERMISSIONS),
    }
