"""Three-way reconciliation: provider bill ↔ normalized cost ↔ invoices.

ReconciliationRun stores computed comparisons per (billing period, payer
account) plus per-customer invoice comparisons; ReconciliationException is the
actionable row with owner/status/notes/evidence/resolution and materiality
flag. Waiving a material exception requires an Approval and is audited.
"""

from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, TimestampMixin
from app.db.money import Money, MoneyN
from app.types import JSONVariant

EXCEPTION_TYPES = (
    "missing_usage",
    "duplicate_usage",
    "unmapped_account",
    "unallocated_credit",
    "rounding_difference",
    "currency_conversion_difference",
    "late_arriving_charge",
    "provider_bill_adjustment",
    "billing_rule_error",
    "uninvoiced_usage",
    "invoice_total_mismatch",
    "missing_billing_period",
    "invalid_currency",
    "missing_tags",
)


class ProviderBillTotal(PkMixin, Base):
    """The provider's own statement of what it charged (the 'ground truth' leg
    of reconciliation), sourced from CUR summary lines or manual entry."""

    __tablename__ = "provider_bill_totals"
    __table_args__ = (
        UniqueConstraint("org_path", "provider_code", "billing_account_ref", "period_start", name="uq_provider_bill"),
    )

    org_path: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_account_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    period_start = mapped_column(DateTime(timezone=True), nullable=False)
    period_end = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    billed_total = Money(default=0)
    credit_total = Money(default=0)
    tax_total = Money(default=0)
    support_total = Money(default=0)
    marketplace_total = Money(default=0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="aws_cur_summary")
    evidence: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class ReconciliationRun(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "reconciliation_runs"

    period_start = mapped_column(DateTime(timezone=True), nullable=False)
    period_end = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    tolerance_abs = MoneyN()  # per-comparison tolerance in contract currency units
    tolerance_pct = MoneyN()
    computed_by = mapped_column(ForeignKey("users.id"), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class ReconciliationException(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "reconciliation_exceptions"
    __table_args__ = (
        UniqueConstraint("run_id", "dedupe_key", name="uq_exception_key"),
    )

    run_id = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    exc_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    materiality: Mapped[str] = mapped_column(String(16), nullable=False, default="material")  # material|minor
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="high")  # low|medium|high|critical
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    billing_account_ref: Mapped[str | None] = mapped_column(String(128))
    invoice_id = mapped_column(ForeignKey("invoices.id"), nullable=True)
    amount_delta = Money(default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    # open|investigating|resolved|waived
    owner_user_id = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at = mapped_column(DateTime(timezone=True), nullable=True)
    waived_by = mapped_column(ForeignKey("users.id"), nullable=True)
    waiver_approval_id = mapped_column(ForeignKey("approvals.id"), nullable=True)
