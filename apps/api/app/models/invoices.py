"""Invoices, invoice lines, notes (credit/debit), disputes, exports.

Invoice lifecycle (docs/invoice-lifecycle.md):
  draft → calculated → under_review → approved → issued → exported
  plus: paid_or_settled, disputed, corrected, voided.

ISSUED invoices are immutable: enforced at the service layer AND by a
Postgres trigger (no UPDATE on invoices/lines where status='issued') from the
migration. Corrections happen via CreditNote/DebitNote or replacement invoice
linked by replaces_id / linked_note_id.

Numbers come from InvoiceSequence (configurable pattern per tenant).
"""

from __future__ import annotations

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.db.money import Money, MoneyN
from app.types import JSONVariant

INVOICE_STATES = (
    "draft",
    "calculated",
    "under_review",
    "approved",
    "issued",
    "exported",
    "paid_or_settled",
    "disputed",
    "corrected",
    "voided",
)


class InvoiceSequence(PkMixin, Base):
    __tablename__ = "invoice_sequences"
    __table_args__ = (UniqueConstraint("org_path", "pattern_key", name="uq_invoice_seq"),)

    org_path: Mapped[str] = mapped_column(String(512), nullable=False)
    pattern_key: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Invoice(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("org_path", "invoice_number", name="uq_invoice_number"),)

    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_family_id = mapped_column(ForeignKey("account_families.id"), nullable=True)
    contract_version_id = mapped_column(ForeignKey("contract_versions.id"), nullable=False)
    pricing_run_id = mapped_column(ForeignKey("pricing_runs.id"), nullable=True, index=True)
    period_start = mapped_column(DateTime(timezone=True), nullable=False)
    period_end = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    grouping: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)

    subtotal = Money(default=0)
    discounts_total = Money(default=0)
    credits_total = Money(default=0)
    fees_total = Money(default=0)
    adjustments_total = Money(default=0)
    taxes_total = Money(default=0)
    prior_period_adjustments_total = Money(default=0)
    total = Money(default=0)
    provider_cost_total = Money(default=0)  # internal — never portal-visible
    margin_total = Money(default=0)         # internal — never portal-visible
    payment_terms: Mapped[str] = mapped_column(String(64), nullable=False, default="Net 30")
    due_date = mapped_column(DateTime(timezone=True), nullable=True)
    notes_customer: Mapped[str | None] = mapped_column(Text)
    notes_internal: Mapped[str | None] = mapped_column(Text)
    branding_snapshot: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)

    issued_at = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text)
    replaces_id = mapped_column(ForeignKey("invoices.id"), nullable=True)
    exported_object_key: Mapped[str | None] = mapped_column(String(1024))


class InvoiceLine(PkMixin, Base):
    __tablename__ = "invoice_lines"

    invoice_id = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="usage")
    # usage | discount | credit | service_fee | adjustment | min_charge | cap | tax | prior_period
    group_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quantity = MoneyN()
    unit: Mapped[str | None] = mapped_column(String(64))
    unit_rate = MoneyN()
    amount = Money(default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    pricing_run_item_id = mapped_column(ForeignKey("pricing_run_items.id"), nullable=True, index=True)
    source_record_ids: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    rule_version_ids: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONVariant, nullable=False, default=dict)
    customer_visible: Mapped[bool] = mapped_column(default=True, nullable=False)


class BillingNote(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Credit or debit note correcting an issued invoice (immutable linkage)."""

    __tablename__ = "billing_notes"

    note_number: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # credit|debit
    invoice_id = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    amount = Money(default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")  # draft|issued|voided
    issued_at = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by = mapped_column(ForeignKey("users.id"), nullable=True)
    lines: Mapped[list[dict]] = mapped_column(JSONVariant, nullable=False, default=list)


class Dispute(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Customer-submitted billing dispute (portal) or internal case."""

    __tablename__ = "disputes"

    dispute_number: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_id = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=True, index=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    submitted_by = mapped_column(ForeignKey("users.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    amount_disputed = MoneyN()
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    # open|investigating|resolved_accepted|resolved_denied|withdrawn
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_at = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by = mapped_column(ForeignKey("users.id"), nullable=True)


class ExportJob(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Data export requests (CSV/PDF/ERP formats) — audited for exports."""

    __tablename__ = "export_jobs"

    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # invoice_csv|invoice_pdf|report_*|erp_*
    parameters: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")  # queued|running|completed|failed
    requested_by = mapped_column(ForeignKey("users.id"), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32))


class PeriodClose(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Billing-period closure: cannot close while material reconciliation
    exceptions are unresolved unless explicitly waived (waiver → Approval)."""

    __tablename__ = "period_closes"
    __table_args__ = (UniqueConstraint("org_path", "billing_period_start", name="uq_period_close"),)

    billing_period_start = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")  # open|closing|closed|reopened
    closed_by = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
