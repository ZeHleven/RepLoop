import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.asyncio
async def test_0029_backfill_roundtrip_and_user_data_guard(db_session):
    connection = await db_session.connection()

    def verify(conn):
        schema = 'energy_migration_' + uuid4().hex
        conn.execute(sa.text(f'CREATE SCHEMA {schema}'))
        conn.execute(sa.text(f'SET LOCAL search_path TO {schema}'))
        conn.execute(sa.text('CREATE TABLE exercises(id varchar PRIMARY KEY, owner_id varchar, category varchar, equipment jsonb)'))
        conn.execute(sa.text('CREATE TABLE workout_sessions(id varchar PRIMARY KEY, status varchar)'))
        conn.execute(sa.text('CREATE TABLE session_exercises(id varchar PRIMARY KEY, exercise_id varchar, sets_data jsonb)'))
        conn.execute(sa.text("""INSERT INTO exercises VALUES ('standard',NULL,'力量','["bodyweight"]'), ('custom','owner','力量','["bodyweight"]'), ('cardio',NULL,'有氧','[]')"""))
        conn.execute(sa.text("INSERT INTO workout_sessions VALUES ('old','completed')"))
        conn.execute(sa.text("""INSERT INTO session_exercises VALUES ('s','standard','[{"reps": 8}]'), ('c','custom','[{"reps": 5}]'), ('a','cardio','[]')"""))
        spec = importlib.util.spec_from_file_location('energy_migration_fixture', Path(__file__).parents[1] / 'alembic/versions/0029_exercise_energy_classification.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.scalar(sa.text("SELECT energy_category FROM session_exercises WHERE id='s'")) == 'bodyweight_resistance'
        assert conn.scalar(sa.text("SELECT energy_category FROM session_exercises WHERE id='c'")) is None
        assert conn.scalar(sa.text("SELECT energy_category_source FROM session_exercises WHERE id='c'")) == 'unclassified'
        assert conn.scalar(sa.text("SELECT energy_category FROM session_exercises WHERE id='a'")) is None
        assert conn.scalar(sa.text("SELECT sets_data FROM session_exercises WHERE id='c'")) == [{'reps':5}]
        migration.downgrade()
        migration.upgrade()
        # Test each independently, including a user clearing their classification again.
        for sql, reset in [
            ("UPDATE exercises SET energy_category_version=1 WHERE id='custom'", "UPDATE exercises SET energy_category_version=0"),
            ("UPDATE workout_sessions SET energy_classification_version=1", "UPDATE workout_sessions SET energy_classification_version=0"),
            ("UPDATE session_exercises SET energy_category_source='user_snapshot' WHERE id='c'", "UPDATE session_exercises SET energy_category_source='unclassified' WHERE id='c'"),
        ]:
            conn.execute(sa.text(sql))
            with pytest.raises(RuntimeError, match='user energy classifications'):
                migration.downgrade()
            conn.execute(sa.text(reset))
        migration.downgrade()
        assert conn.scalar(sa.text("SELECT status FROM workout_sessions")) == 'completed'
    await connection.run_sync(verify)
