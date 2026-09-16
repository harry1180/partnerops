"""Identity & access: users, roles, permissions, scoped role assignments.

Design (ADR 0005): role-based with per-organization assignment so a single
identity can hold different roles in different tenants (customer admin of one
MSP's portal vs. analyst of the MSP itself). The authorization layer resolves
effective permissions to a permission string set cached per request; the model
is deliberately ready for attribute-based extensions (ABAC) via the
`attributes` JSON column on assignments in a later phase.

Local auth uses opaque session tokens (SHA-256 hash stored server-side);
the architecture supports OIDC/SAML by swapping the IdentityProvider adapter
in app/services/authz.py. Password hashing: argon2id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PkMixin, SoftDeleteMixin, TimestampMixin
from app.types import JSONVariant


class User(PkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)  # null → SSO-only
    home_org_id = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text)  # MFA-ready
    mfa_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column()
    last_login_at: Mapped[datetime | None] = mapped_column()
    agreed_terms_at: Mapped[datetime | None] = mapped_column()


ROLE_CATALOG: dict[str, str] = {
    "platform_admin": "Full platform administration",
    "distributor_admin": "Manage distributor org and its resellers",
    "msp_admin": "Manage reseller org: customers, contracts, billing, invoices",
    "finops_analyst": "Cost analysis, optimization, governance for assigned scope",
    "billing_analyst": "Pricing runs, invoices, reconciliation for assigned scope",
    "customer_admin": "Portal administration for one customer (users, budgets, disputes)",
    "customer_readonly": "Portal read-only for one customer",
    "auditor": "Read-only cross-tenant audit evidence within granted scope",
}


class Role(PkMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    is_customer_facing: Mapped[bool] = mapped_column(default=False, nullable=False)


class Permission(PkMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")


class RolePermission(PkMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    role_id = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_id = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)


class UserRoleAssignment(PkMixin, TimestampMixin, Base):
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "org_id", name="uq_user_role_org"),
    )

    user_id = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    attributes: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)  # ABAC-ready


class Session(PkMixin, Base):
    """Server-side session store keyed by hash of the opaque cookie token."""

    __tablename__ = "sessions"

    user_id = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column()


class ApiToken(PkMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Scoped integration tokens (Authorization: Bearer cpo_...)."""

    __tablename__ = "api_tokens"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    org_id = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    created_by = mapped_column(ForeignKey("users.id"), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column()
    expires_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()


__all__ = [
    "User",
    "Role",
    "Permission",
    "RolePermission",
    "UserRoleAssignment",
    "Session",
    "ApiToken",
    "ROLE_CATALOG",
    "uuid",
]
