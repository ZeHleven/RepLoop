"""Freeze exercise energy classifications; explicit user metadata is downgrade-protected."""
from alembic import op
import sqlalchemy as sa

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('exercises', sa.Column('energy_category', sa.String(32), nullable=True))
    op.add_column('exercises', sa.Column('energy_category_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('workout_sessions', sa.Column('energy_classification_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('session_exercises', sa.Column('energy_category', sa.String(32), nullable=True))
    op.add_column('session_exercises', sa.Column('energy_category_source', sa.String(32), nullable=True))
    op.add_column('session_exercises', sa.Column('energy_rule_version', sa.String(32), nullable=True))
    for table in ('exercises', 'session_exercises'):
        op.create_check_constraint(f'ck_{table}_energy_category', table,
                                   "energy_category IS NULL OR energy_category IN ('resistance_training', 'bodyweight_resistance')")
    op.execute("""UPDATE session_exercises AS s SET
        energy_category = CASE WHEN e.owner_id IS NULL AND e.category IN ('力量','核心')
            THEN CASE WHEN e.equipment = '["bodyweight"]'::jsonb
                THEN 'bodyweight_resistance' ELSE 'resistance_training' END ELSE NULL END,
        energy_category_source = CASE WHEN e.owner_id IS NULL THEN 'standard_mapping' ELSE 'unclassified' END,
        energy_rule_version = 'strength_met_v1'
        FROM exercises AS e WHERE e.id = s.exercise_id""")


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("""SELECT EXISTS(SELECT 1 FROM exercises WHERE energy_category_version > 0 OR energy_category IS NOT NULL)
        OR EXISTS(SELECT 1 FROM workout_sessions WHERE energy_classification_version > 0)
        OR EXISTS(SELECT 1 FROM session_exercises WHERE energy_category_source IN ('user_snapshot','user_correction'))""")):
        raise RuntimeError('0029 contains user energy classifications; refusing to discard user data')
    for table in ('exercises', 'session_exercises'):
        op.drop_constraint(f'ck_{table}_energy_category', table, type_='check')
    for column in ('energy_rule_version', 'energy_category_source', 'energy_category'):
        op.drop_column('session_exercises', column)
    op.drop_column('workout_sessions', 'energy_classification_version')
    op.drop_column('exercises', 'energy_category_version')
    op.drop_column('exercises', 'energy_category')
