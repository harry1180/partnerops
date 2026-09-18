"""FinOps + governance domain (Phase 4).

Design commitments:
- Budgets are per-scope (org subtree: partner-level or customer-level) with a
  spend threshold; variance = actual vs straight-line burn over the period.
- Anomalies come from an honest statistical method (robust z-score on daily
  spend vs trailing median/MAD, charter: "understandable statistical methods
  and configurable thresholds"; no ML is claimed).
- Recommendations are computed from canonical cost rows (rightsizing from
  observed max daily utilization proxies, idle from low-activity patterns,
  commitment coverage gaps); accept/dismiss is audited; savings realization
  is MEASURED against later spend, never projected. The platform never acts
  on provider resources.
- Governance: policies (required tags, approved regions, idle/oversized)
  evaluate canonical rows into findings with evidence; exceptions carry
  owner + expiry and auto-close findings while active.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.db.money import Money, MoneyN
from app.types import JSONVariant

BUDGET_SCOPES = ("org", "customer", "account_family")
ANOMALY_KINDS = ("cost_spike", "cost_drop", "unbilled_spike", "credit_movement")
ANOMALY_STATUSES = ("open", "acknowledged", "false_positive", "resolved")
REC_KINDS = ("rightsizing", "idle_resource", "commitment_gap", "storage_class", "unused_ip", "marketplace_review")
REC_STATUSES = ("open", "accepted", "dismissed", "realized")
POLICY_KINDS = ("required_tags", "approved_regions", "unencrypted_evidence", "idle_resources",
                "oversized_resources", "budget_violation", "expired_exception")
POLICY_SEVERITIES = ("info", "low", "medium", "high", "critical")
FINDING_STATUSES = ("open", "acknowledged", "remediated", "excepted")


class Budget(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Spend budget over a scope and calendar period, provider-total."""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("org_path", "name", "period_start", name="uq_budget_name_period"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="customer")
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    account_family_id = mapped_column(ForeignKey("account_families.id"), nullable=True)
    cloud_account_id = mapped_column(ForeignKey("cloud_accounts.id"), nullable=True)
    provider_code: Mapped[str | None] = mapped_column(String(32))  # None = all providers
    amount = Money(default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    period_start = mapped_column(DateTime(timezone=True), nullable=False)
    period_end = mapped_column(DateTime(timezone=True), nullable=False)
    alert_threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # Phase 7 alert episode state (JSON): {"breached": bool,
    # "last_alert_at": iso, "alert_count": n}. Null = never alerted.
    alert_state: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_by = mapped_column(ForeignKey("users.id"), nullable=True)


class CostAnomaly(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Statistical anomaly on daily provider-billed spend, computed by the
    detection pass over canonical rows. Deterministic given (data, config)."""

    __tablename__ = "cost_anomalies"
    __table_args__ = (
        UniqueConstraint("org_path", "dedupe_key", name="uq_anomaly_dedupe"),
        Index("ix_anomaly_period", "org_path", "detected_on"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="cost_spike")
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    cloud_account_id = mapped_column(ForeignKey("cloud_accounts.id"), nullable=True)
    service: Mapped[str | None] = mapped_column(String(255))
    detected_on = mapped_column(DateTime(timezone=True), nullable=False)
    observed_amount = Money(default=0)
    baseline_amount = Money(default=0)
    z_score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    threshold_used: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=28)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    method: Mapped[str] = mapped_column(String(64), nullable=False, default="robust_zscore_median_mad")
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at = mapped_column(DateTime(timezone=True), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class Recommendation(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Optimization suggestion computed from canonical cost rows. Accept/
    dismiss workflow is audited; realized_savings only ever set from
    measured before/after spend by the savings-realization pass."""

    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("org_path", "dedupe_key", name="uq_rec_dedupe"),
        Index("ix_rec_status", "org_path", "status"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    cloud_account_id = mapped_column(ForeignKey("cloud_accounts.id"), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(512))
    service: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remediation: Mapped[str | None] = mapped_column(Text)
    estimated_monthly_saving = MoneyN()
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")  # low|medium|high
    basis: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    decided_by = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text)
    realized_savings = MoneyN()
    realized_at = mapped_column(DateTime(timezone=True), nullable=True)
    realized_basis: Mapped[dict] = mapped_column(JSONVariant, nullable=True)


class GovernancePolicy(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "governance_policies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    owner_label: Mapped[str | None] = mapped_column(String(255))
    parameters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    remediation: Mapped[str | None] = mapped_column(Text)
    created_by = mapped_column(ForeignKey("users.id"), nullable=True)
    last_evaluated_at = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("severity IN ('info','low','medium','high','critical')", name="ck_policy_severity"),
    )


class GovernanceFinding(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """One policy violation against one subject (account/customer/resource).
    Re-evaluation upserts by dedupe_key so status/history survive passes."""

    __tablename__ = "governance_findings"
    __table_args__ = (
        UniqueConstraint("org_path", "dedupe_key", name="uq_finding_dedupe"),
        Index("ix_finding_status", "org_path", "status"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    policy_id = mapped_column(ForeignKey("governance_policies.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    cloud_account_id = mapped_column(ForeignKey("cloud_accounts.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    first_seen = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen = mapped_column(DateTime(timezone=True), nullable=False)
    remediation: Mapped[str | None] = mapped_column(Text)
    owner_label: Mapped[str | None] = mapped_column(String(255))
    acknowledged_by = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text)


class PolicyException(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Time-boxed exemption for a finding: active while not expired; the
    next evaluation pass marks excepted findings and expired exceptions."""

    __tablename__ = "policy_exceptions"
    __table_args__ = (
        UniqueConstraint("org_path", "finding_id", name="uq_exception_finding"),
    )

    finding_id = mapped_column(ForeignKey("governance_findings.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at = mapped_column(DateTime(timezone=True), nullable=True)
