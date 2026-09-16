"""Versioned contracts, billing rules and rule versions.

Contract modification ALWAYS creates a new ContractVersion row; a version that
has been used by a completed pricing run is immutable. Overlapping active
versions are rejected at the service layer unless an authorized administrator
explicitly approves the overlap (approval record required).

Billing rules carry a priority and calculation order; every rule change is a
new BillingRuleVersion. Rules are versioned independently of contracts so a
contract can pin a rule-set snapshot via ContractVersion.rule_bindings.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.db.money import MoneyN
from app.types import JSONVariant

PRICING_BASES = ("provider_billed", "list", "ondemand_equivalent", "net_after_credits")
CADENCES = ("monthly", "quarterly", "annual", "usage_based_monthly")


class Contract(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "contracts"

    customer_id = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")  # draft|active|expired|terminated
    notes: Mapped[str | None] = mapped_column(Text)


class ContractVersion(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "contract_versions"
    __table_args__ = (UniqueConstraint("contract_id", "version_number", name="uq_contract_version"),)

    contract_id = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")  # draft|pending_approval|active|superseded|rejected
    effective_start = mapped_column(DateTime(timezone=True), nullable=False)
    effective_end = mapped_column(DateTime(timezone=True), nullable=True)  # null = open-ended
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    billing_cadence: Mapped[str] = mapped_column(String(32), nullable=False, default="monthly")
    payment_terms: Mapped[str] = mapped_column(String(64), nullable=False, default="Net 30")
    pricing_basis: Mapped[str] = mapped_column(String(64), nullable=False, default=PRICING_BASES[0])
    discount_policy: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    credit_sharing_policy: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    commitment_sharing_policy: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    support_fee_policy: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)  # pass_through|remove|replace
    tax_behavior: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    managed_service_fee: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    minimum_monthly = MoneyN()
    invoice_grouping: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    rounding_rule: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=lambda: {"mode": "half_up", "level": "line", "increment": "0.01"})
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    custom_rates: Mapped[list[dict]] = mapped_column(JSONVariant, nullable=False, default=list)
    rule_bindings: Mapped[list[dict]] = mapped_column(JSONVariant, nullable=False, default=list)  # [{rule_id, rule_version_id, order}]
    overlap_approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at = mapped_column(DateTime(timezone=True), nullable=True)


class BillingRule(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "billing_rules"

    contract_id = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=True, index=True)
    customer_id = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")  # draft|pending_approval|published|retired
    notes: Mapped[str | None] = mapped_column(Text)


RULE_TYPES = (
    "percentage_markup",
    "percentage_discount",
    "fixed_recurring",
    "one_time",
    "fixed_unit_rate",
    "tiered",
    "minimum_monthly",
    "maximum_cap",
    "managed_service_fee",
    "support_charge",
    "credit_pass_through",
    "credit_retention",
    "custom_service_charge",
    "sku_override",
    "marketplace_adjustment",
    "tax_adjustment",
    "currency_conversion",
    "data_exclusion",
    "promotional_credit",
    "manual_adjustment",
)


class BillingRuleVersion(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "billing_rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version_number", name="uq_rule_version"),)

    rule_id = mapped_column(ForeignKey("billing_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")  # draft|pending_approval|published|retired|rejected
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)  # lower runs earlier
    calc_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    applied_basis: Mapped[str] = mapped_column(String(64), nullable=False, default="running_total")  # running_total|original_input
    filters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    parameters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    effective_start = mapped_column(DateTime(timezone=True), nullable=True)
    effective_end = mapped_column(DateTime(timezone=True), nullable=True)
    high_impact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estimated_impact_monthly = MoneyN()  # from last sandbox test
    created_by = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)


class RuleSandboxTest(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Result of a Test-rule sandbox run against historical data — evidence
    attached to approval requests for high-impact rule changes."""

    __tablename__ = "rule_sandbox_tests"

    rule_id = mapped_column(ForeignKey("billing_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    rule_version_id = mapped_column(ForeignKey("billing_rule_versions.id", ondelete="CASCADE"), nullable=False)
    tested_by = mapped_column(ForeignKey("users.id"), nullable=False)
    period_start = mapped_column(DateTime(timezone=True), nullable=False)
    period_end = mapped_column(DateTime(timezone=True), nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    result_summary: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
