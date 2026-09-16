"""Approvals (maker-checker), notifications outbox, and integration configs.

ApprovalRequest is the generic maker-checker record used by: high-impact rule
changes, contract overlaps, manual adjustments, reconciliation waivers, period
close waivers, and (future) any AI-assistant write action. Maker ≠ checker is
enforced in the service layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.db.money import MoneyN
from app.types import JSONVariant


class ApprovalRequest(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "approvals"

    request_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # rule_publish | contract_overlap | contract_activate | manual_adjustment
    # | recon_waiver | period_close_waiver | invoice_issue | ai_write
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    impact_amount = MoneyN()
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    # pending|approved|denied|cancelled
    maker_id = mapped_column(ForeignKey("users.id"), nullable=False)
    checker_id = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class Integration(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """Configured external connections (S3 bucket, ERP, ServiceNow, Slack…).
    Secrets are stored as object-storage/KMS references or encrypted values —
    never plaintext (validated by tests + code review checklist)."""

    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("org_path", "kind", "name", name="uq_integration_name"),)

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured")
    config: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    secret_refs: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    last_check_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class WebhookEndpoint(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "webhook_endpoints"

    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False, default=list)
    secret_ref: Mapped[str | None] = mapped_column(String(255))  # HMAC signing secret reference
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    description: Mapped[str | None] = mapped_column(String(512))


class WebhookDelivery(PkMixin, Base):
    __tablename__ = "webhook_deliveries"

    endpoint_id = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    response_code: Mapped[int | None] = mapped_column()
    created_at = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationOutbox(PkMixin, TimestampMixin, OrgScopedMixin, Base):
    """Internal notification/email queue (email delivery is stubbed locally;
    transport adapters are integration boundaries, not fake buttons)."""

    __tablename__ = "notification_outbox"

    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="email")
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")  # queued|sent|failed
    sent_at = mapped_column(DateTime(timezone=True), nullable=True)
