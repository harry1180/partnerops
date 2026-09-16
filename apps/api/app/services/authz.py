"""Authorization: permission catalog, role→permission mapping, and the
IdentityProvider seam that keeps OIDC/SAML adoption additive.

RBAC is the shipped model; ABAC readiness: every permission decision receives
a RequestPrincipal carrying (roles, org subtree scope, attributes dict) so
attribute predicates can be layered in without changing call sites.

NEVER encode margin/entitlement logic in the UI alone: `margin.view`,
`internal_notes.view`, and `partner_data.view` are the gates that keep
customer users from seeing partner economics; serializers must check them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------- permissions
PERMISSIONS: dict[str, str] = {
    # tenancy & administration
    "platform.manage": "Create and manage any organization and user on the platform",
    "org.manage": "Manage organizations inside own subtree",
    "user.manage": "Manage users inside own subtree",
    "branding.manage": "Configure white-label branding inside own subtree",
    "integration.manage": "Configure integrations and webhooks",
    "api_token.manage": "Issue and rotate scoped API tokens",
    # core business data
    "customer.read": "View customers/accounts/usage in own scope",
    "customer.write": "Create and edit customers, account families, cloud accounts",
    "cost.read": "View canonical cost and usage records",
    "contract.read": "View contracts and billing rules",
    "contract.write": "Create and version contracts",
    "contract.approve": "Approve contract activations and overlaps",
    "rule.write": "Create and version billing rules",
    "rule.publish": "Publish rule versions (still subject to maker-checker)",
    "rule.approve": "Approve high-impact rule changes (must differ from maker)",
    "pricing.run": "Run/reprocess pricing for a period",
    "invoice.read": "View invoices within scope",
    "invoice.write": "Draft and calculate invoices",
    "invoice.approve": "Approve invoices for issue",
    "invoice.issue": "Issue invoices (immutable after this transition)",
    "invoice.correct": "Issue credit/debit notes and corrections",
    "adjustment.manual": "Create manual adjustments (subject to approval)",
    "recon.read": "View reconciliation runs and exceptions",
    "recon.resolve": "Resolve reconciliation exceptions",
    "recon.waive": "Waive material exceptions / approve period close",
    "period.close": "Close and reopen billing periods",
    # analytics & partner economics
    "margin.view": "View partner cost, margin and retained-benefit data (partner roles only)",
    "internal_notes.view": "View internal-only notes (partner roles only)",
    "partner_data.view": "View provider-side commercial information",
    "report.read": "Run and download reports",
    "report.schedule": "Schedule reports",
    "export.data": "Export data (audited)",
    "alert.manage": "Configure analytics alerts",
    "budget.manage": "Create and edit budgets",
    "anomaly.review": "Review and annotate anomalies",
    "optimization.review": "Accept/dismiss optimization recommendations",
    "policy.manage": "Create and edit governance policies",
    "finding.review": "Review governance findings and exceptions",
    "dispute.read": "View disputes in scope",
    "dispute.write": "Create and respond to disputes",
    "dispute.resolve": "Resolve disputes",
    # audit + assistant
    "audit.read": "Read the audit trail within scope",
    "assistant.ask": "Ask the PartnerOps assistant (answers restricted by permissions)",
    "assistant.ask_sensitive": "Ask queries that touch margin or cross-customer data",
}

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "platform_admin": ["*"],
    "distributor_admin": [
        "org.manage", "user.manage", "branding.manage", "integration.manage", "api_token.manage",
        "customer.read", "customer.write", "cost.read", "contract.read", "contract.write",
        "contract.approve", "rule.write", "rule.publish", "rule.approve", "pricing.run",
        "invoice.read", "invoice.write", "invoice.approve", "invoice.issue", "invoice.correct",
        "adjustment.manual", "recon.read", "recon.resolve", "recon.waive", "period.close",
        "margin.view", "internal_notes.view", "partner_data.view", "report.read", "report.schedule",
        "export.data", "alert.manage", "budget.manage", "anomaly.review", "optimization.review",
        "policy.manage", "finding.review", "dispute.read", "dispute.write", "dispute.resolve",
        "audit.read", "assistant.ask", "assistant.ask_sensitive",
    ],
    "msp_admin": [
        "org.manage", "user.manage", "branding.manage", "integration.manage", "api_token.manage",
        "customer.read", "customer.write", "cost.read", "contract.read", "contract.write",
        "contract.approve", "rule.write", "rule.publish", "rule.approve", "pricing.run",
        "invoice.read", "invoice.write", "invoice.approve", "invoice.issue", "invoice.correct",
        "adjustment.manual", "recon.read", "recon.resolve", "recon.waive", "period.close",
        "margin.view", "internal_notes.view", "partner_data.view", "report.read", "report.schedule",
        "export.data", "alert.manage", "budget.manage", "anomaly.review", "optimization.review",
        "policy.manage", "finding.review", "dispute.read", "dispute.write", "dispute.resolve",
        "audit.read", "assistant.ask", "assistant.ask_sensitive",
    ],
    "finops_analyst": [
        "customer.read", "cost.read", "contract.read", "recon.read", "margin.view",
        "report.read", "export.data", "alert.manage", "budget.manage", "anomaly.review",
        "optimization.review", "policy.manage", "finding.review", "assistant.ask",
    ],
    "billing_analyst": [
        "customer.read", "customer.write", "cost.read", "contract.read", "contract.write",
        "rule.write", "pricing.run", "invoice.read", "invoice.write", "recon.read",
        "recon.resolve", "margin.view", "internal_notes.view", "report.read", "export.data",
        "dispute.read", "dispute.write", "assistant.ask",
    ],
    "customer_admin": [
        "customer.read", "cost.read", "invoice.read", "report.read", "export.data",
        "budget.manage", "dispute.read", "dispute.write", "assistant.ask", "optimization.review",
        "finding.review",
    ],
    "customer_readonly": ["customer.read", "cost.read", "invoice.read", "report.read", "assistant.ask"],
    "auditor": [
        "customer.read", "cost.read", "contract.read", "invoice.read", "recon.read",
        "report.read", "export.data", "audit.read", "dispute.read", "margin.view", "assistant.ask",
    ],
}


@dataclass(frozen=True)
class RequestPrincipal:
    """Everything the authorization layer knows about the caller.

    `attributes` is the ABAC seam: future predicates (data classification,
    time-of-day, IP) read from here without touching call sites.
    """

    user_id: uuid.UUID
    email: str
    org_id: uuid.UUID
    org_path: str
    org_kind: str
    roles: frozenset[str]
    permissions: frozenset[str]
    scope_prefixes: tuple[str, ...] = field(default_factory=tuple)
    is_platform_admin: bool = False
    attributes: dict = field(default_factory=dict)

    def can(self, permission: str) -> bool:
        return self.is_platform_admin or permission in self.permissions


def expand_role_permissions(roles: set[str]) -> set[str]:
    out: set[str] = set()
    for role in roles:
        perms = ROLE_PERMISSIONS.get(role, [])
        if "*" in perms:
            return set(PERMISSIONS.keys())
        out.update(perms)
    return out


# ------------------------------------------------------------ identity seam
class IdentityProvider(Protocol):
    """AuthN adapter interface. LocalIdentityProvider ships in Phase 0;
    OIDC and SAML providers implement this without touching call sites.

    (SAML arrives with an IdP-initiated ACS route; OIDC with an authorization
    code callback — both will call into a session-issuing path identical to
    auth_service.create_session.)"""

    key: str

    async def authenticate(self, session: AsyncSession, credentials: dict) -> object | None: ...


class LocalIdentityProvider:
    key = "local"

    async def authenticate(self, session: AsyncSession, credentials: dict) -> object | None:
        from app.services.auth_service import verify_local_credentials

        return await verify_local_credentials(
            session, str(credentials.get("email", "")), str(credentials.get("password", ""))
        )


LOCAL_IDP = LocalIdentityProvider()
