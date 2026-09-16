"""Canonical Postgres JSON type (JSONB) usable across sync/async engines."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON, TypeDecorator


class JSONVariant(TypeDecorator[object]):
    """JSON stored as JSONB on PostgreSQL, plain JSON elsewhere (tests)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())
