"""Add reviewed foods and independent manual-browse metadata, preserving snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from app.services.food_catalog_v1 import sync_catalog, remove_imported_catalog
revision='0031'
down_revision='0030'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('foods',sa.Column('browse_category',sa.String(30),nullable=True))
    op.add_column('foods',sa.Column('browse_aliases',JSONB(),nullable=True))
    op.add_column('foods',sa.Column('source_info',JSONB(),nullable=True))
    op.create_index('ix_foods_browse_category','foods',['browse_category'])
    op.create_table('food_catalog_imports',sa.Column('catalog_key',sa.String(100),primary_key=True),
        sa.Column('food_id',sa.String(),sa.ForeignKey('foods.id'),nullable=False,unique=True),
        sa.Column('created_by_import',sa.Boolean(),nullable=False),sa.Column('initial_snapshot',JSONB(),nullable=False))
    sync_catalog(op.get_bind())


def downgrade():
    remove_imported_catalog(op.get_bind())
    op.drop_table('food_catalog_imports')
    op.drop_index('ix_foods_browse_category',table_name='foods')
    for column in ('source_info','browse_aliases','browse_category'):op.drop_column('foods',column)
