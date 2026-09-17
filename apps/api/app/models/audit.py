"""Immutable audit trail.

Append-only by application policy and by a Postgres rule (see migration):
UPDATE/DELETE on audit_events are revoked from the app role and rejected by a
trigger. Audit events reference org subtrees via org_path; auditor queries
filter by their granted scopes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PkMixin, TimestampMixin
from app.types import JSONVariant

AUDIT_ACTIONS = (
    "login.success",
    "login.failure",
    "logout",
    "user.created",
    "user.role_changed",
    "user.disabled",
    "contract.created",
    "contract.version_created",
    "billing_rule.created",
    "billing_rule.version_created",
    "billing_rule.published",
    "pricing_run.started",
    "pricing_run.completed",
    "invoice.created",
    "invoice.state_changed",
    "invoice.issued",
    "invoice.corrected",
    "adjustment.manual",
    "approval.requested",
    "approval.granted",
    "approval.denied",
    "reconciliation.waived",
    "dispute.created",
    "dispute.resolved",
    "export.data",
    "integration.changed",
    "period.closed",
    "tenant.scope_denied",
    "ai.query",
    "seed.demo_data",
    "ingestion.file_parsed",
    "ingestion.file_failed",
    "pricing.started",
    "pricing.completed",
    "reconciliation.completed",
    "reconciliation.exception_updated",
    "account_family.created",
    "cloud_account.mapped",
)


class AuditEvent(PkMixin, TimestampMixin, Base):
    __tablename__ = "audit_events"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="system")  # user|system|api_token|job
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
