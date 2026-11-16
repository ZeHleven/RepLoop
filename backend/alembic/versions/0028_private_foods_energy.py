"""Private food library and daily activity preference; meal snapshots remain intact."""
from alembic import op
import sqlalchemy as sa

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('custom_foods',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        *[sa.Column(field, sa.Float(), nullable=False) for field in
          ('amount_g', 'calories', 'protein_g', 'carbs_g', 'fat_g')],
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('client_request_id', sa.String(100), nullable=False),
        sa.Column('creation_fingerprint', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'client_request_id', name='uq_custom_food_request'),
    )
    op.create_index('ix_custom_foods_user_id', 'custom_foods', ['user_id'])
    op.add_column('meal_items', sa.Column('custom_food_id', sa.String(), nullable=True))
    op.add_column('meal_items', sa.Column('custom_food_version', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_meal_items_custom_food', 'meal_items', 'custom_foods', ['custom_food_id'], ['id'])
    op.add_column('user_profiles', sa.Column('daily_activity_level', sa.String(20), nullable=True))


def downgrade():
    # A populated private library cannot be discarded by an accidental downgrade.
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM custom_foods)')):
        raise RuntimeError('0028 contains private foods; retain the schema and roll forward instead of deleting user data')
    op.drop_column('user_profiles', 'daily_activity_level')
    op.drop_constraint('fk_meal_items_custom_food', 'meal_items', type_='foreignkey')
    op.drop_column('meal_items', 'custom_food_version')
    op.drop_column('meal_items', 'custom_food_id')
    op.drop_index('ix_custom_foods_user_id', table_name='custom_foods')
    op.drop_table('custom_foods')
