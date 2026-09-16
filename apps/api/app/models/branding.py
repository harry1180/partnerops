"""White-label branding configuration per tenant subtree.

Resolution walks up the org path: the nearest configured branding wins, with
platform defaults as fallback. All UI surfaces (console chrome, reports,
invoices, emails) read branding through this model only — no hardcoded brand
strings in components (enforced by ADR 0007).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OrgScopedMixin, PkMixin, SoftDeleteMixin, TimestampMixin
from app.types import JSONVariant


class BrandingConfig(PkMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, Base):
    __tablename__ = "branding_configs"

    product_name: Mapped[str] = mapped_column(String(128), nullable=False, default="Cloud PartnerOps")
    logo_object_key: Mapped[str | None] = mapped_column(String(512))  # in object storage
    favicon_object_key: Mapped[str | None] = mapped_column(String(512))
    primary_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#0F3D5C")
    accent_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#C9A227")
    support_email: Mapped[str | None] = mapped_column(String(320))
    email_sender_name: Mapped[str | None] = mapped_column(String(128))
    custom_domain: Mapped[str | None] = mapped_column(String(255))
    terminology: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    feature_flags: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    invoice_branding: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    org_id_override = mapped_column(ForeignKey("organizations.id"), nullable=True)
