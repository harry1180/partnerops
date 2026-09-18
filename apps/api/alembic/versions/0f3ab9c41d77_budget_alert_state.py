"""budget alert episode state

Phase 7: budget.over_threshold events + outbox notifications need
per-budget alert state (one alert per breach episode, re-arms when spend
falls back below the threshold). Stored as a JSON column on budgets —
no new table, no new RLS surface.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app import types as app_types  # noqa: F401

revision: str = "0f3ab9c41d77"
down_revision: Union[str, None] = "8bd7518cfa18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("budgets",
                  sa.Column("alert_state", app_types.JSONVariant(), nullable=True))


def downgrade() -> None:
    op.drop_column("budgets", "alert_state")
