"""finops governance

Revision ID: 8bd7518cfa18
Revises: c9a4d1e7f302
Create Date: 2026-09-18 10:40:41.892907

"""
from alembic import op
import sqlalchemy as sa
from app.db import money as app_money  # noqa: F401
from app import types as app_types  # noqa: F401


revision = '8bd7518cfa18'
down_revision = 'c9a4d1e7f302'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('governance_policies',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('owner_label', sa.String(length=255), nullable=True),
    sa.Column('parameters', app_types.JSONVariant(), nullable=False),
    sa.Column('remediation', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('org_path', sa.String(length=512), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("severity IN ('info','low','medium','high','critical')", name='ck_policy_severity'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_governance_policies_created_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_governance_policies_org_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_governance_policies'))
    )
    op.create_index(op.f('ix_governance_policies_org_id'), 'governance_policies', ['org_id'], unique=False)
    op.create_index(op.f('ix_governance_policies_org_path'), 'governance_policies', ['org_path'], unique=False)
    op.create_table('budgets',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('scope_kind', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('account_family_id', sa.Uuid(), nullable=True),
    sa.Column('cloud_account_id', sa.Uuid(), nullable=True),
    sa.Column('provider_code', sa.String(length=32), nullable=True),
    sa.Column('amount', app_money.MoneyNumeric(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('alert_threshold_pct', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('org_path', sa.String(length=512), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['account_family_id'], ['account_families.id'], name=op.f('fk_budgets_account_family_id_account_families')),
    sa.ForeignKeyConstraint(['cloud_account_id'], ['cloud_accounts.id'], name=op.f('fk_budgets_cloud_account_id_cloud_accounts')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_budgets_created_by_users')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_budgets_customer_id_customers')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_budgets_org_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_budgets')),
    sa.UniqueConstraint('org_path', 'name', 'period_start', name='uq_budget_name_period')
    )
    op.create_index(op.f('ix_budgets_customer_id'), 'budgets', ['customer_id'], unique=False)
    op.create_index(op.f('ix_budgets_org_id'), 'budgets', ['org_id'], unique=False)
    op.create_index(op.f('ix_budgets_org_path'), 'budgets', ['org_path'], unique=False)
    op.create_table('cost_anomalies',
    sa.Column('dedupe_key', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('cloud_account_id', sa.Uuid(), nullable=True),
    sa.Column('service', sa.String(length=255), nullable=True),
    sa.Column('detected_on', sa.DateTime(timezone=True), nullable=False),
    sa.Column('observed_amount', app_money.MoneyNumeric(), nullable=False),
    sa.Column('baseline_amount', app_money.MoneyNumeric(), nullable=False),
    sa.Column('z_score', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('threshold_used', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('window_days', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('method', sa.String(length=64), nullable=False),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('evidence', app_types.JSONVariant(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('org_path', sa.String(length=512), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['cloud_account_id'], ['cloud_accounts.id'], name=op.f('fk_cost_anomalies_cloud_account_id_cloud_accounts')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_cost_anomalies_customer_id_customers')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_cost_anomalies_org_id_organizations')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_cost_anomalies_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cost_anomalies')),
    sa.UniqueConstraint('org_path', 'dedupe_key', name='uq_anomaly_dedupe')
    )
    op.create_index('ix_anomaly_period', 'cost_anomalies', ['org_path', 'detected_on'], unique=False)
    op.create_index(op.f('ix_cost_anomalies_customer_id'), 'cost_anomalies', ['customer_id'], unique=False)
    op.create_index(op.f('ix_cost_anomalies_org_id'), 'cost_anomalies', ['org_id'], unique=False)
    op.create_index(op.f('ix_cost_anomalies_org_path'), 'cost_anomalies', ['org_path'], unique=False)
    op.create_table('governance_findings',
    sa.Column('dedupe_key', sa.String(length=160), nullable=False),
    sa.Column('policy_id', sa.Uuid(), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('cloud_account_id', sa.Uuid(), nullable=True),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('evidence', app_types.JSONVariant(), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('remediation', sa.Text(), nullable=True),
    sa.Column('owner_label', sa.String(length=255), nullable=True),
    sa.Column('acknowledged_by', sa.Uuid(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('org_path', sa.String(length=512), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id'], name=op.f('fk_governance_findings_acknowledged_by_users')),
    sa.ForeignKeyConstraint(['cloud_account_id'], ['cloud_accounts.id'], name=op.f('fk_governance_findings_cloud_account_id_cloud_accounts')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_governance_findings_customer_id_customers')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_governance_findings_org_id_organizations')),
    sa.ForeignKeyConstraint(['policy_id'], ['governance_policies.id'], name=op.f('fk_governance_findings_policy_id_governance_policies'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_governance_findings')),
    sa.UniqueConstraint('org_path', 'dedupe_key', name='uq_finding_dedupe')
    )
    op.create_index('ix_finding_status', 'governance_findings', ['org_path', 'status'], unique=False)
    op.create_index(op.f('ix_governance_findings_customer_id'), 'governance_findings', ['customer_id'], unique=False)
    op.create_index(op.f('ix_governance_findings_org_id'), 'governance_findings', ['org_id'], unique=False)
    op.create_index(op.f('ix_governance_findings_org_path'), 'governance_findings', ['org_path'], unique=False)
    op.create_index(op.f('ix_governance_findings_policy_id'), 'governance_findings', ['policy_id'], unique=False)
    op.create_table('recommendations',
    sa.Column('dedupe_key', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.Uuid(), nullable=True),
    sa.Column('cloud_account_id', sa.Uuid(), nullable=True),
    sa.Column('resource_id', sa.String(length=512), nullable=True),
    sa.Column('service', sa.String(length=255), nullable=True),
    sa.Column('region', sa.String(length=64), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('remediation', sa.Text(), nullable=True),
    sa.Column('estimated_monthly_saving', app_money.MoneyNumeric(), nullable=True),
    sa.Column('confidence', sa.String(length=16), nullable=False),
    sa.Column('basis', app_types.JSONVariant(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('realized_savings', app_money.MoneyNumeric(), nullable=True),
    sa.Column('realized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('realized_basis', app_types.JSONVariant(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('org_path', sa.String(length=512), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['cloud_account_id'], ['cloud_accounts.id'], name=op.f('fk_recommendations_cloud_account_id_cloud_accounts')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_recommendations_customer_id_customers')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_recommendations_decided_by_users')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_recommendations_org_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recommendations')),
    sa.UniqueConstraint('org_path', 'dedupe_key', name='uq_rec_dedupe')
    )
    op.create_index('ix_rec_status', 'recommendations', ['org_path', 'status'], unique=False)
    op.create_index(op.f('ix_recommendations_customer_id'), 'recommendations', ['customer_id'], unique=False)
    op.create_index(op.f('ix_recommendations_org_id'), 'recommendations', ['org_id'], unique=False)
    op.create_index(op.f('ix_recommendations_org_path'), 'recommendations', ['org_path'], unique=False)
    op.create_table('policy_exceptions',
    sa.Column('finding_id', sa.Uuid(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('approved_by', sa.Uuid(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('org_path', sa.String(length=512), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_policy_exceptions_approved_by_users')),
    sa.ForeignKeyConstraint(['finding_id'], ['governance_findings.id'], name=op.f('fk_policy_exceptions_finding_id_governance_findings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_policy_exceptions_org_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_policy_exceptions')),
    sa.UniqueConstraint('org_path', 'finding_id', name='uq_exception_finding')
    )
    op.create_index(op.f('ix_policy_exceptions_finding_id'), 'policy_exceptions', ['finding_id'], unique=False)
    op.create_index(op.f('ix_policy_exceptions_org_id'), 'policy_exceptions', ['org_id'], unique=False)
    op.create_index(op.f('ix_policy_exceptions_org_path'), 'policy_exceptions', ['org_path'], unique=False)

    # indexes the Phase 2/3 hand-written migrations skipped (model declares
    # index=True on org_id; PG has these from this revision onward)
    op.create_index(op.f('ix_provider_connectors_org_id'), 'provider_connectors', ['org_id'], unique=False)
    op.create_index(op.f('ix_report_schedules_org_id'), 'report_schedules', ['org_id'], unique=False)

    # RLS: same subtree policy as every tenant-scoped table (Postgres only —
    # the sqlite test harness has no RLS; app-layer scoping is still asserted)
    if op.get_bind().dialect.name == "postgresql":
        for t in ("budgets", "cost_anomalies", "recommendations",
                  "governance_policies", "governance_findings", "policy_exceptions"):
            op.execute(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY')
            op.execute(f"""
                CREATE POLICY tenant_scope ON "{t}"
                  USING (
                    left(org_path, length(current_setting('app.current_org_path', true)))
                    = current_setting('app.current_org_path', true)
                  )
                  WITH CHECK (
                    left(org_path, length(current_setting('app.current_org_path', true)))
                    = current_setting('app.current_org_path', true)
                  )
            """)
            op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO partnerops')


def downgrade() -> None:
    op.drop_index(op.f('ix_report_schedules_org_id'), table_name='report_schedules')
    op.drop_index(op.f('ix_provider_connectors_org_id'), table_name='provider_connectors')
    op.drop_index(op.f('ix_policy_exceptions_org_path'), table_name='policy_exceptions')
    op.drop_index(op.f('ix_policy_exceptions_org_id'), table_name='policy_exceptions')
    op.drop_index(op.f('ix_policy_exceptions_finding_id'), table_name='policy_exceptions')
    op.drop_table('policy_exceptions')
    op.drop_index(op.f('ix_recommendations_org_path'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_org_id'), table_name='recommendations')
    op.drop_index(op.f('ix_recommendations_customer_id'), table_name='recommendations')
    op.drop_index('ix_rec_status', table_name='recommendations')
    op.drop_table('recommendations')
    op.drop_index(op.f('ix_governance_findings_policy_id'), table_name='governance_findings')
    op.drop_index(op.f('ix_governance_findings_org_path'), table_name='governance_findings')
    op.drop_index(op.f('ix_governance_findings_org_id'), table_name='governance_findings')
    op.drop_index(op.f('ix_governance_findings_customer_id'), table_name='governance_findings')
    op.drop_index('ix_finding_status', table_name='governance_findings')
    op.drop_table('governance_findings')
    op.drop_index(op.f('ix_cost_anomalies_org_path'), table_name='cost_anomalies')
    op.drop_index(op.f('ix_cost_anomalies_org_id'), table_name='cost_anomalies')
    op.drop_index(op.f('ix_cost_anomalies_customer_id'), table_name='cost_anomalies')
    op.drop_index('ix_anomaly_period', table_name='cost_anomalies')
    op.drop_table('cost_anomalies')
    op.drop_index(op.f('ix_budgets_org_path'), table_name='budgets')
    op.drop_index(op.f('ix_budgets_org_id'), table_name='budgets')
    op.drop_index(op.f('ix_budgets_customer_id'), table_name='budgets')
    op.drop_table('budgets')
    op.drop_index(op.f('ix_governance_policies_org_path'), table_name='governance_policies')
    op.drop_index(op.f('ix_governance_policies_org_id'), table_name='governance_policies')
    op.drop_table('governance_policies')
