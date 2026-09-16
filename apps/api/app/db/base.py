"""Declarative base and shared column mixins.

Conventions (see docs/decisions/0004-primary-keys-and-timestamps.md):
- UUID primary keys (no sequential ids leaked across tenants)
- created_at / updated_at maintained by the DB (server_default + trigger-free
  onupdate via SQLAlchemy)
- Soft deletion via `deleted_at` on business records; queries must filter.
- Tenant-scoped tables carry `org_path` (materialized hierarchy path) which
  is what the PostgreSQL RLS policies evaluate.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        uuid.UUID: Uuid,
        datetime: DateTime(timezone=True),
    }


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PkMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class OrgScopedMixin:
    """Rows visible only inside the organization subtree named by org_path."""

    org_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    # owning organization id kept alongside for joins / UI
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


def short(id_: uuid.UUID) -> str:
    return str(id_)[:8]


__all__ = ["Base", "TimestampMixin", "SoftDeleteMixin", "PkMixin", "OrgScopedMixin", "Integer"]
