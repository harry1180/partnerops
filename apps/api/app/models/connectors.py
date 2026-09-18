"""Cloud-provider connector configuration (Phase 3).

A connector row is the per-partner statement of *how* provider billing data
arrives: which provider, which billing account it covers, the cadence we
expect a fresh export on, and the synthetic-mode flag that lets local dev
import deterministic fixtures with zero cloud credentials.

Honesty rules baked into the design (ADR-0016):
- fetch_status/fetch_message describe what is actually implemented. Live
  fetch is False in this build; no endpoint ever pretends to pull from AWS
  or Azure. "Last seen" data always comes from a real uploaded/synthetic
  file with a real sha256.
- due-check compares the connector's cadence against the LAST SUCCESSFUL
  INGESTION of a file for that billing account — evidence-based staleness,
  not aspiration.
"""

from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.types import JSONVariant

CONNECTOR_KINDS = ("aws_cur", "azure_cost_export", "gcp_billing")
CONNECTOR_MODES = ("synthetic", "manual_upload")  # live connectors: later phases
CADENCES = ("daily", "weekly", "monthly")


class ProviderConnector(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    """One row per provider billing relationship we expect data for."""

    __tablename__ = "provider_connectors"
    __table_args__ = (
        UniqueConstraint("org_path", "provider_code", "billing_account_ref",
                         name="uq_connector_billing_account"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)  # aws|azure
    connector_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="synthetic")
    billing_account_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # 1..28
    hour_utc: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by = mapped_column(ForeignKey("users.id"), nullable=True)
    last_ingest_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_file_id = mapped_column(ForeignKey("raw_billing_files.id"), nullable=True)
    next_due_at = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    config: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
