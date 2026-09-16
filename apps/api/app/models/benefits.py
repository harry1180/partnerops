"""Credits, discounts, commitments, and allocation policies.

Benefits (RI/Savings Plan/reservation/EDP/promo/service credits) are tracked
here and allocated to invoices according to the contract's sharing policies.
Commitment PURCHASE execution is intentionally out of scope for release 1 —
only recommendation + approval interfaces exist (Phase 4/2), via a controlled
connector seam.
"""

from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.db.money import Money, MoneyN
from app.types import JSONVariant

COMMITMENT_KINDS = (
    "aws_reserved_instance",
    "aws_savings_plan",
    "azure_reservation",
    "azure_savings_plan",
    "enterprise_discount_program",
    "private_pricing_agreement",
    "volume_discount",
)

CREDIT_KINDS = (
    "promotional",
    "service",
    "refund",
    "marketplace",
    "edp_true_up",
    "manual",
)

SHARING_MODES = (
    "pass_full",
    "pass_partial",
    "retain",
    "owner_allocated",
    "proportional",
    "eligible_usage",
    "custom",
)


class Commitment(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "commitments"

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False, default="aws")
    billing_account_id = mapped_column(ForeignKey("cloud_billing_accounts.id"), nullable=True, index=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)  # owner if dedicated
    start_date = mapped_column(DateTime(timezone=True), nullable=False)
    end_date = mapped_column(DateTime(timezone=True), nullable=True)
    hourly_commitment = MoneyN()
    upfront_paid = MoneyN()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    coverage_scope: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)  # services/regions/family
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text)


class Credit(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Provider-issued credit/refund balance (promo funds, good-will credits,
    refunds). Allocation status tracked for the DQ dashboard."""

    __tablename__ = "credits"
    __table_args__ = (UniqueConstraint("org_path", "provider_code", "external_id", name="uq_credit_external"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="service")
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_account_id = mapped_column(ForeignKey("cloud_billing_accounts.id"), nullable=True, index=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    amount_total = Money(default=0)
    amount_used = Money(default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    effective_start = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at = mapped_column(DateTime(timezone=True), nullable=True)
    allocation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unallocated", index=True)
    source_file_id = mapped_column(ForeignKey("raw_billing_files.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)


class DiscountProgram(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """EDP / private pricing / volume discount programs with rate tables."""

    __tablename__ = "discount_programs"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_account_id = mapped_column(ForeignKey("cloud_billing_accounts.id"), nullable=True, index=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="enterprise_discount_program")
    rates: Mapped[list[dict]] = mapped_column(JSONVariant, nullable=False, default=list)
    effective_start = mapped_column(DateTime(timezone=True), nullable=False)
    effective_end = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class AllocationPolicy(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """How a benefit class is shared: fully/partially passed, retained,
    allocated to commitment owner, proportional, eligible-usage based, or a
    custom script-like parameter set (evaluated by the billing engine)."""

    __tablename__ = "allocation_policies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    benefit_class: Mapped[str] = mapped_column(String(64), nullable=False)  # commitment|credit|discount|support|marketplace
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default=SHARING_MODES[0])
    share_pct = MoneyN()  # for pass_partial
    parameters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
