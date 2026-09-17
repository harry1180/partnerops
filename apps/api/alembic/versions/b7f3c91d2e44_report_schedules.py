"""report schedules

Revision ID: b7f3c91d2e44
Revises: 5ae8b26da9ad
Create Date: 2026-09-17 01:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from app.db import money as app_money  # noqa: F401
from app import types as app_types  # noqa: F401


revision = 'b7f3c91d2e44'
down_revision = '5ae8b26da9ad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'report_schedules',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('report_key', sa.String(length=64), nullable=False),
        sa.Column('cadence', sa.String(length=16), nullable=False),
        sa.Column('day_of_month', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('hour_utc', sa.Integer(), nullable=False),
        sa.Column('recipients', app_types.JSONVariant(), nullable=False),
        sa.Column('period_offset_months', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('org_path', sa.String(length=512), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name=op.f('fk_report_schedules_created_by_users')),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'],
                                name=op.f('fk_report_schedules_org_id_organizations')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_report_schedules')),
        sa.UniqueConstraint('org_path', 'name', name='uq_report_schedule_name'),
    )
    op.create_index(op.f('ix_report_schedules_next_run_at'), 'report_schedules',
                    ['next_run_at'], unique=False)
    op.create_index(op.f('ix_report_schedules_org_path'), 'report_schedules',
                    ['org_path'], unique=False)

    # RLS: same subtree policy as every tenant-scoped table (Postgres only —
    # the sqlite test harness has no RLS; app-layer scoping is still asserted)
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "report_schedules" ENABLE ROW LEVEL SECURITY')
        op.execute("""
            CREATE POLICY tenant_scope ON "report_schedules"
              USING (
                left(org_path, length(current_setting('app.current_org_path', true)))
                = current_setting('app.current_org_path', true)
              )
              WITH CHECK (
                left(org_path, length(current_setting('app.current_org_path', true)))
                = current_setting('app.current_org_path', true)
              )
        """)
        # the app role gets DML via default privileges; belt-and-braces grant
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON report_schedules TO partnerops')


def downgrade() -> None:
    op.drop_index(op.f('ix_report_schedules_org_path'), table_name='report_schedules')
    op.drop_index(op.f('ix_report_schedules_next_run_at'), table_name='report_schedules')
    op.drop_table('report_schedules')
