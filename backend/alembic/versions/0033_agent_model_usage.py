"""Provider HTTP usage separate from controller budgets; old rows stay unknown."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_runs', sa.Column('model_usage', JSONB(), nullable=True))


def downgrade():
    op.drop_column('agent_runs', 'model_usage')
