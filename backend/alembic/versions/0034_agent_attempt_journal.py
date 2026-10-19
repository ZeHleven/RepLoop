"""Crash-durable observations; existing attempts remain explicitly unobserved."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('agent_attempt_journals',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('run_id', sa.String(), sa.ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('worker_attempt', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('model_usage', JSONB(), nullable=True),
        sa.Column('tool_calls', JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.UniqueConstraint('run_id', 'worker_attempt', name='uq_agent_journal_attempt'),
        sa.CheckConstraint('worker_attempt >= 1', name='ck_agent_journal_attempt'))
    op.create_index('ix_agent_attempt_journals_run_id', 'agent_attempt_journals', ['run_id'])

def downgrade():
    op.drop_table('agent_attempt_journals')
