"""Money-safe numeric column: fixed-precision Decimal ↔ NUMERIC(20,6).

Never Float for money or quantity (see charter + ADR 0003).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Numeric
from sqlalchemy.orm import mapped_column
from sqlalchemy.types import TypeDecorator

MONEY_SCALE = 6


class MoneyNumeric(TypeDecorator[Decimal]):
    """NUMERIC(20,6) with explicit precision; Python side is always Decimal."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 20, scale: int = MONEY_SCALE) -> None:
        super().__init__()
        self._spec = (precision, scale)

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        return dialect.type_descriptor(Numeric(*self._spec))


def Money(**kw: object):  # type: ignore[no-untyped-def]  # noqa: ANN201 - mapped_column factory
    return mapped_column(MoneyNumeric(), nullable=False, **kw)  # type: ignore[arg-type]


def MoneyN(**kw: object):  # type: ignore[no-untyped-def]
    return mapped_column(MoneyNumeric(), nullable=True, **kw)  # type: ignore[arg-type]
