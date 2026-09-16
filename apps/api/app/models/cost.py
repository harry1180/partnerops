"""Ingestion: raw files, raw records, canonical cost records, quarantine.

Design (ADR 0008):
- raw_billing_files carries checksum (sha256), source, object-storage key,
  parser version, and status → idempotent re-ingest keyed on checksum; the raw
  file object is retained for audit.
- raw_billing_records keeps each source row verbatim in `payload` (JSONB).
- canonical_cost_records is the provider-neutral model; `source_record_id`
  gives row-level lineage, and (source_record_id, dedupe_key) unique index
  detects duplicates.
- Late-arriving adjustments are new rows with an earlier usage window; the
  pricing/reconciliation layer detects them by comparing period snapshots.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PkMixin, TimestampMixin
from app.db.money import Money, MoneyN
from app.types import JSONVariant

FILE_STATUSES = ("uploaded", "parsing", "parsed", "failed", "quarantined", "duplicate")
INGEST_SOURCES = ("synthetic", "aws_cur_s3", "aws_manual_upload", "azure_cost_export", "azure_manual_upload")


class RawBillingFile(PkMixin, TimestampMixin, Base):
    __tablename__ = "raw_billing_files"
    __table_args__ = (UniqueConstraint("sha256", "parser_version", name="uq_file_checksum_parser"),)

    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(index=True)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="synthetic")
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_period_start = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period_end = mapped_column(DateTime(timezone=True), nullable=True)
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded", index=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(32))
    ingested_by = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column()


class RawBillingRecord(PkMixin, Base):
    __tablename__ = "raw_billing_records"
    __table_args__ = (
        UniqueConstraint("file_id", "row_number", name="uq_raw_row_position"),
        Index("ix_raw_records_provider_key", "provider_code", "source_record_id"),
    )

    file_id = mapped_column(ForeignKey("raw_billing_files.id", ondelete="CASCADE"), nullable=False, index=True)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 of semantic identity
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class QuarantinedRecord(PkMixin, Base):
    __tablename__ = "quarantined_records"
    __table_args__ = (Index("ix_quarantine_status_period", "status", "billing_period_start"),)

    file_id = mapped_column(ForeignKey("raw_billing_files.id", ondelete="CASCADE"), nullable=True, index=True)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(64), nullable=False)  # invalid_currency|missing_account|bad_quantity
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)  # open|resolved|ignored
    resolved_by = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column()
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CanonicalCostRecord(PkMixin, Base):
    """The normalized, provider-neutral cost & usage model (charter list).

    All amounts are NUMERIC — no floats. Amount semantics:
      list_cost              provider public price × quantity
      ondemand_equivalent    what usage would cost at on-demand rates
      provider_billed        what the provider actually charged (unblended)
      amortized              commitment amortization applied
      effective              amortized minus exclusions (provider view)
      net                    provider_billed - credits (partner cash view)
      credit / tax / support_fee / marketplace_fee: components (may be negative)
    """

    __tablename__ = "canonical_cost_records"
    __table_args__ = (
        UniqueConstraint("source_record_id", "lineage_file_id", name="uq_canonical_source_row"),
        Index("ix_ccr_period_account", "billing_period_start", "cloud_account_id"),
        Index("ix_ccr_period_customer", "billing_period_start", "customer_id"),
        Index("ix_ccr_org_path_period", "org_path", "billing_period_start"),
        Index("ix_ccr_service", "service"),
    )

    lineage_file_id = mapped_column(ForeignKey("raw_billing_files.id", ondelete="CASCADE"), nullable=False, index=True)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_period_start = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end = mapped_column(DateTime(timezone=True), nullable=False)
    usage_start = mapped_column(DateTime(timezone=True), nullable=False)
    usage_end = mapped_column(DateTime(timezone=True), nullable=False)
    invoice_id: Mapped[str | None] = mapped_column(String(128))
    payer_or_billing_account: Mapped[str | None] = mapped_column(String(128))
    billing_account_id = mapped_column(ForeignKey("cloud_billing_accounts.id"), nullable=True)
    cloud_account_id = mapped_column(ForeignKey("cloud_accounts.id"), nullable=True, index=True)
    account_family_id = mapped_column(ForeignKey("account_families.id"), nullable=True)
    customer_id = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(512))
    service: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(255))
    usage_type: Mapped[str | None] = mapped_column(String(255))
    operation: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(128))
    availability_zone: Mapped[str | None] = mapped_column(String(128))
    quantity = MoneyN()
    unit: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    line_item_type: Mapped[str] = mapped_column(String(64), nullable=False, default="usage")
    # usage | discount | credit | refund | tax | fee | support | marketplace |
    # reservation_recurring | savings_plan_recurring | adjustment
    cost_category: Mapped[str | None] = mapped_column(String(64))  # compute|storage|database|network|…

    list_cost = Money(default=0)
    ondemand_equivalent = Money(default=0)
    provider_billed = Money(default=0)
    amortized = Money(default=0)
    effective = Money(default=0)
    net = Money(default=0)
    credit = Money(default=0)
    tax = Money(default=0)
    support_fee = Money(default=0)
    marketplace_fee = Money(default=0)

    tags: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    application: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(64))
    owner: Mapped[str | None] = mapped_column(String(255))
    cost_center: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)  # provider-native fields
    is_late_adjustment: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
