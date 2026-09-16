"""Pricing runs and calculation lineage.

A PricingRun snapshots: contract version + rule version ids + engine version +
inputs (billing period, account set). PricingRunItem stores one per-line
computation with FULL lineage (source record ids, inputs, formula, output,
actor, approval status) so every invoice amount is traceable to source usage
and a versioned billing rule. Runs are append-only; reprocessing a period
creates a NEW run and links the previous one via supersedes_id.
"""

from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, TimestampMixin
from app.db.money import Money, MoneyN
from app.types import JSONVariant


class PricingRun(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "pricing_runs"
    __table_args__ = (UniqueConstraint("org_path", "customer_id", "period_start", "run_number", name="uq_run_number"),)

    customer_id = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    contract_version_id = mapped_column(ForeignKey("contract_versions.id", ondelete="RESTRICT"), nullable=False)
    period_start = mapped_column(DateTime(timezone=True), nullable=False)
    period_end = mapped_column(DateTime(timezone=True), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_id = mapped_column(ForeignKey("pricing_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    # queued|running|completed|failed|superseded
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    triggered_by = mapped_column(ForeignKey("users.id"), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32))
    input_summary: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    totals: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)  # money as strings
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_required")


class PricingRunItem(PkMixin, Base):
    __tablename__ = "pricing_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "item_number", name="uq_run_item_number"),
    )

    run_id = mapped_column(ForeignKey("pricing_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    item_number: Mapped[int] = mapped_column(Integer, nullable=False)
    group_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")  # invoice grouping key
    group_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    line_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="usage")
    # usage | discount | credit | fee | min_charge | cap | tax | adjustment | exclusion
    source_record_ids: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    rule_version_ids: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    input_amount = MoneyN()
    output_amount = Money(default=0)
    provider_cost_amount = Money(default=0)
    formula: Mapped[str] = mapped_column(Text, nullable=False, default="")  # human-readable calculation
    calculation_trace: Mapped[list[dict]] = mapped_column(JSONVariant, nullable=False, default=list)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONVariant, nullable=False, default=dict)


class PricingRunRuleSnapshot(PkMixin, Base):
    """Immutable snapshot of each rule version that participated in a run —
    the exact parameters used, so historical explanations never depend on
    later rule edits."""

    __tablename__ = "pricing_run_rule_snapshots"

    run_id = mapped_column(ForeignKey("pricing_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rule_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parameters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    filters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    calc_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
