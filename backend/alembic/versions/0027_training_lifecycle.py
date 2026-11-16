"""Training family/week context, archive visibility and private custom exercises.

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('workout_plans', sa.Column('family_id', sa.String(), nullable=True))
    op.add_column('workout_plans', sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=True))
    # Applied proposal links are evidence of lineage; names alone are not.
    op.execute("""
        WITH RECURSIVE ancestry AS (
          SELECT id AS descendant, id AS ancestor, user_id, ARRAY[id] AS visited
          FROM workout_plans
          UNION ALL
          SELECT a.descendant, p.base_plan_id, a.user_id, a.visited || p.base_plan_id
          FROM ancestry a JOIN agent_proposals p
            ON p.result_plan_id = a.ancestor AND p.user_id = a.user_id
          WHERE p.status = 'applied'
            AND p.proposal_type IN ('plan_adjustment_v1', 'plan_adjustment_v2')
            AND p.base_plan_id IS NOT NULL AND NOT p.base_plan_id = ANY(a.visited)
        ), roots AS (
          SELECT DISTINCT ON (descendant) descendant, ancestor
          FROM ancestry ORDER BY descendant, cardinality(visited) DESC, ancestor
        )
        UPDATE workout_plans w SET family_id = r.ancestor FROM roots r
        WHERE r.descendant = w.id
    """)
    op.alter_column('workout_plans', 'family_id', nullable=False)
    op.create_index('ix_workout_plans_family_id', 'workout_plans', ['family_id'])
    op.add_column('workout_sessions', sa.Column('plan_family_id', sa.String(), nullable=True))
    op.add_column('workout_sessions', sa.Column('week_start', sa.Date(), nullable=True))
    op.execute("""
        UPDATE workout_sessions s SET plan_family_id = p.family_id,
          week_start = s.trained_at - (extract(isodow from s.trained_at)::integer - 1)
        FROM workout_plans p WHERE s.plan_id = p.id AND s.user_id = p.user_id
    """)
    op.create_index('ix_workout_sessions_plan_family_id', 'workout_sessions', ['plan_family_id'])
    op.create_index('ix_workout_sessions_week_context', 'workout_sessions', ['user_id', 'plan_family_id', 'week_start', 'day_of_week'])
    op.add_column('session_exercises', sa.Column('exercise_name', sa.String(100), nullable=True))
    op.execute('UPDATE session_exercises s SET exercise_name = e.name_zh FROM exercises e WHERE s.exercise_id = e.id')
    op.add_column('exercises', sa.Column('owner_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_exercises_owner', 'exercises', 'users', ['owner_id'], ['id'])
    op.create_index('ix_exercises_owner_id', 'exercises', ['owner_id'])


def downgrade():
    # Old code has no owner predicate. Fail closed instead of exposing private rows.
    connection = op.get_bind()
    if connection.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM exercises WHERE owner_id IS NOT NULL)')):
        raise RuntimeError('0027 cannot be downgraded while private exercises exist; retain 0027 and roll forward')
    op.drop_index('ix_exercises_owner_id', table_name='exercises')
    op.drop_constraint('fk_exercises_owner', 'exercises', type_='foreignkey')
    op.drop_column('exercises', 'owner_id')
    op.drop_column('session_exercises', 'exercise_name')
    op.drop_index('ix_workout_sessions_week_context', table_name='workout_sessions')
    op.drop_index('ix_workout_sessions_plan_family_id', table_name='workout_sessions')
    op.drop_column('workout_sessions', 'week_start')
    op.drop_column('workout_sessions', 'plan_family_id')
    op.drop_index('ix_workout_plans_family_id', table_name='workout_plans')
    op.drop_column('workout_plans', 'hidden_at')
    op.drop_column('workout_plans', 'family_id')
