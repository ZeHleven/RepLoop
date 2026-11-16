"""Separate conversation enqueue order from wall/transaction timestamps."""
from alembic import op
import sqlalchemy as sa

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('agent_runs', sa.Column('queue_position', sa.BigInteger(), nullable=True))
    op.create_unique_constraint('uq_agent_run_conversation_position', 'agent_runs', ['conversation_id','queue_position'])
    op.create_check_constraint('ck_agent_run_queue_position','agent_runs','queue_position IS NULL OR queue_position > 0')

def downgrade():
    op.drop_constraint('ck_agent_run_queue_position','agent_runs',type_='check')
    op.drop_constraint('uq_agent_run_conversation_position','agent_runs',type_='unique')
    op.drop_column('agent_runs','queue_position')
