"""Exercise the real 0027 migration on connection-local legacy tables."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.asyncio
async def test_0027_preserves_lineage_history_and_blocks_private_data_downgrade(db_session):
    connection = await db_session.connection()

    def verify(conn):
        # pg_temp shadows public names on this connection only. No public table
        # is altered, and all fixture tables disappear on rollback/connection close.
        for statement in [
            'CREATE TEMP TABLE users (id varchar PRIMARY KEY) ON COMMIT DROP',
            'CREATE TEMP TABLE workout_plans (id varchar PRIMARY KEY, user_id varchar, name varchar) ON COMMIT DROP',
            'CREATE TEMP TABLE agent_proposals (base_plan_id varchar, result_plan_id varchar, user_id varchar, status varchar, proposal_type varchar) ON COMMIT DROP',
            'CREATE TEMP TABLE workout_sessions (id varchar PRIMARY KEY, user_id varchar, plan_id varchar, trained_at date, status varchar, day_of_week integer) ON COMMIT DROP',
            'CREATE TEMP TABLE exercises (id varchar PRIMARY KEY, name_zh varchar) ON COMMIT DROP',
            'CREATE TEMP TABLE session_exercises (id varchar PRIMARY KEY, exercise_id varchar) ON COMMIT DROP',
            "INSERT INTO users VALUES ('owner'), ('other')",
            "INSERT INTO workout_plans VALUES ('p1','owner','原名'), ('p2','owner','调整1'), ('p3','owner','调整2'), ('foreign','other','其他用户')",
            "INSERT INTO agent_proposals VALUES ('p1','p2','owner','applied','plan_adjustment_v1'), ('p2','p3','owner','applied','plan_adjustment_v2'), ('foreign','p1','other','applied','plan_adjustment_v2')",
            "INSERT INTO workout_sessions VALUES ('s1','owner','p1','2026-09-06','completed',1), ('s2','owner','p2','2026-09-07','completed',1), ('orphan','owner',null,'2026-09-07','in_progress',1)",
            "INSERT INTO exercises VALUES ('e1','旧动作名')",
            "INSERT INTO session_exercises VALUES ('se1','e1')",
        ]:
            conn.execute(sa.text(statement))
        path = Path(__file__).parents[1] / 'alembic/versions/0027_training_lifecycle.py'
        spec = importlib.util.spec_from_file_location('training_migration_fixture', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert dict(conn.execute(sa.text('SELECT id, family_id FROM workout_plans')).all()) == {
            'p1': 'p1', 'p2': 'p1', 'p3': 'p1', 'foreign': 'foreign',
        }
        rows = conn.execute(sa.text('SELECT id, plan_family_id, week_start::text, status FROM workout_sessions ORDER BY id')).all()
        assert rows == [('orphan', None, None, 'in_progress'), ('s1', 'p1', '2026-08-31', 'completed'), ('s2', 'p1', '2026-09-07', 'completed')]
        assert conn.scalar(sa.text("SELECT exercise_name FROM session_exercises WHERE id='se1'")) == '旧动作名'
        conn.execute(sa.text("UPDATE exercises SET owner_id='owner' WHERE id='e1'"))
        with pytest.raises(RuntimeError, match='private exercises'):
            migration.downgrade()
        assert conn.scalar(sa.text("SELECT owner_id FROM exercises WHERE id='e1'")) == 'owner'
        conn.execute(sa.text("UPDATE exercises SET owner_id=null WHERE id='e1'"))
        migration.downgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM workout_sessions')) == 3
        assert conn.scalar(sa.text('SELECT count(*) FROM session_exercises')) == 1

    await connection.run_sync(verify)
