"""Organization hierarchy: platform → distributor → reseller/MSP → customer.

`path` is the materialized path (e.g. "/platform-id/dist-id/res-id/") used by
RLS and subtree queries. `depth` denormalizes the kind ordering for checks.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PkMixin, SoftDeleteMixin, TimestampMixin

KINDS = ("platform", "distributor", "reseller", "customer", "internal")


class Organization(PkMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("parent_id", "name", name="uq_org_parent_name"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    parent_id = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True, index=True)
    path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    billing_profile: Mapped[str | None] = mapped_column(String(64))  # e.g. "reseller", "end_customer"
