"""Expand strength/core catalogue without renaming or mutating legacy exercise snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from app.services.exercise_catalog_v1 import sync_catalog, remove_imported_catalog

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('exercise_catalog_imports',
                    sa.Column('catalog_key', sa.String(100), primary_key=True),
                    sa.Column('exercise_id', sa.String(), sa.ForeignKey('exercises.id'), nullable=False, unique=True),
                    sa.Column('created_by_import', sa.Boolean(), nullable=False),
                    sa.Column('initial_snapshot', JSONB(), nullable=False))
    sync_catalog(op.get_bind())


def downgrade():
    remove_imported_catalog(op.get_bind())
    op.drop_table('exercise_catalog_imports')
