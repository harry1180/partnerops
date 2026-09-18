"""provider connectors

Revision ID: c9a4d1e7f302
Revises: b7f3c91d2e44
Create Date: 2026-09-17 09:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
from app.db import money as app_money  # noqa: F401
from app import types as app_types  # noqa: F401


revision = 'c9a4d1e7f302'
down_revision = 'b7f3c91d2e44'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 3: provider_bill_totals gains a grain level — the enrollment/'invoice'
    # statement vs a per-cloud-account rollup (multi-account reconciliation).
    op.add_column(
        "provider_bill_totals",
        sa.Column("level", sa.String(length=16), nullable=False, server_default="invoice"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("uq_provider_bill", "provider_bill_totals", type_="unique")
        op.create_unique_constraint(
            "uq_provider_bill", "provider_bill_totals",
            ["org_path", "provider_code", "billing_account_ref", "period_start", "level"],
        )
    op.create_table(
        'provider_connectors',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('provider_code', sa.String(length=32), nullable=False),
        sa.Column('connector_kind', sa.String(length=64), nullable=False),
        sa.Column('mode', sa.String(length=32), nullable=False),
        sa.Column('billing_account_ref', sa.String(length=128), nullable=False),
        sa.Column('cadence', sa.String(length=16), nullable=False),
        sa.Column('day_of_month', sa.Integer(), nullable=False),
        sa.Column('hour_utc', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('last_ingest_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_file_id', sa.Uuid(), nullable=True),
        sa.Column('next_due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('config', app_types.JSONVariant(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('org_path', sa.String(length=512), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name=op.f('fk_provider_connectors_created_by_users')),
        sa.ForeignKeyConstraint(['last_file_id'], ['raw_billing_files.id'],
                                name=op.f('fk_provider_connectors_last_file_id_raw_billing_files')),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'],
                                name=op.f('fk_provider_connectors_org_id_organizations')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_provider_connectors')),
        sa.UniqueConstraint('org_path', 'provider_code', 'billing_account_ref',
                            name='uq_connector_billing_account'),
    )
    op.create_index(op.f('ix_provider_connectors_next_due_at'), 'provider_connectors',
                    ['next_due_at'], unique=False)
    op.create_index(op.f('ix_provider_connectors_org_path'), 'provider_connectors',
                    ['org_path'], unique=False)

    # RLS: same subtree policy as every tenant-scoped table (Postgres only —
    # the sqlite test harness has no RLS; app-layer scoping is still asserted)
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "provider_connectors" ENABLE ROW LEVEL SECURITY')
        op.execute("""
            CREATE POLICY tenant_scope ON "provider_connectors"
              USING (
                left(org_path, length(current_setting('app.current_org_path', true)))
                = current_setting('app.current_org_path', true)
              )
              WITH CHECK (
                left(org_path, length(current_setting('app.current_org_path', true)))
                = current_setting('app.current_org_path', true)
              )
        """)
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON provider_connectors TO partnerops')


def downgrade() -> None:
    op.drop_index(op.f('ix_provider_connectors_org_path'), table_name='provider_connectors')
    op.drop_index(op.f('ix_provider_connectors_next_due_at'), table_name='provider_connectors')
    op.drop_table('provider_connectors')
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("uq_provider_bill", "provider_bill_totals", type_="unique")
        op.create_unique_constraint(
            "uq_provider_bill", "provider_bill_totals",
            ["org_path", "provider_code", "billing_account_ref", "period_start"],
        )
    op.drop_column("provider_bill_totals", "level")
