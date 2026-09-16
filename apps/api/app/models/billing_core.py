"""Customer, cloud account and FinOps dimension models.

Hierarchy: Organization(customer) ← AccountFamily ← CloudAccount/Subscription.
FinOps dimensions (Application, Environment, Owner, CostCenter) are first-class
rows per customer so allocation is auditable, not just tag strings.
"""

from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.types import JSONVariant


class Customer(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """A billable end customer. `org_id` points at the customer Organization
    node (kind=customer); the owning partner org is captured via org_path."""

    __tablename__ = "customers"

    code: Mapped[str] = mapped_column(String(64), nullable=False)  # short partner-facing code
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_email: Mapped[str | None] = mapped_column(String(320))
    po_number: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)  # internal only — never portal-visible
    target_margin_pct = mapped_column(Numeric(6, 2), nullable=True)  # e.g. 18.50 = 18.5%
    portal_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    billing_contact_name: Mapped[str | None] = mapped_column(String(255))
    billing_address: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class AccountFamily(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Billing grouping of one or more cloud accounts/subscriptions belonging
    to a customer (e.g. 'prod-workloads', 'migrated-legacy')."""

    __tablename__ = "account_families"
    __table_args__ = (UniqueConstraint("org_path", "name", name="uq_family_name_per_customer"),)

    customer_id = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class CloudProvider(PkMixin, TimestampMixin, Base):
    __tablename__ = "cloud_providers"

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)  # aws|azure|gcp
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")  # available|planned


class CloudBillingAccount(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Provider-side billing relationship (AWS payer account / Azure EA or MCA).
    Belongs to the PARTNER org, not the customer: it is where the provider
    sends the bill."""

    __tablename__ = "cloud_billing_accounts"

    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_prefix: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    extra: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class CloudAccount(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Linked AWS account / Azure subscription at the finest common grain."""

    __tablename__ = "cloud_accounts"
    __table_args__ = (
        UniqueConstraint("org_path", "provider_code", "external_id", name="uq_account_external"),
    )

    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_account_id = mapped_column(ForeignKey("cloud_billing_accounts.id"), nullable=True, index=True)
    account_family_id = mapped_column(ForeignKey("account_families.id"), nullable=True, index=True)
    # allocation state — unmapped accounts are surfaced on the DQ dashboard
    allocation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unmapped")
    mapped_at = mapped_column(DateTime(timezone=True), nullable=True)
    account_kind: Mapped[str | None] = mapped_column(String(32))  # member|payer|management…
    organization_path: Mapped[str | None] = mapped_column(String(512))  # AWS OU path from Organizations
    extra: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class Subscription(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Azure subscription (or any sub-grain container under an account)."""

    __tablename__ = "subscriptions"

    cloud_account_id = mapped_column(ForeignKey("cloud_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)


class ResourceGroup(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "resource_groups"

    subscription_id = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class FinopsDimension(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Application / Environment / Owner / Cost-center dimensions per customer.
    `kind` keeps one table for all four with a check constraint."""

    __tablename__ = "finops_dimensions"
    __table_args__ = (UniqueConstraint("org_path", "kind", "value", name="uq_dimension_value"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # application|environment|owner|cost_center
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_id = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(255))
