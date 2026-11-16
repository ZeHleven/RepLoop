import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.asyncio
async def test_0028_roundtrip_and_populated_library_guard(db_session):
    connection = await db_session.connection()

    def verify(conn):
        schema = 'nutrition_migration_' + uuid4().hex
        # Transaction-local fixture schema; rollback removes it without touching public data.
        conn.execute(sa.text(f'CREATE SCHEMA {schema}'))
        conn.execute(sa.text(f'SET LOCAL search_path TO {schema}'))
        conn.execute(sa.text('CREATE TABLE users (id varchar PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE user_profiles (id varchar PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE meal_items (id varchar PRIMARY KEY, calories float)'))
        conn.execute(sa.text("INSERT INTO users VALUES ('owner')"))
        conn.execute(sa.text("INSERT INTO meal_items VALUES ('old-meal-item', 123.4)"))
        spec = importlib.util.spec_from_file_location('nutrition_migration_fixture', Path(__file__).parents[1] / 'alembic/versions/0028_private_foods_energy.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.scalar(sa.text('SELECT calories FROM meal_items')) == 123.4
        assert conn.scalar(sa.text('SELECT custom_food_id FROM meal_items')) is None
        conn.execute(sa.text("INSERT INTO custom_foods (id,user_id,name,amount_g,calories,protein_g,carbs_g,fat_g,client_request_id,creation_fingerprint) VALUES ('private','owner','food',100,100,1,2,3,'request1','fingerprint')"))
        with pytest.raises(RuntimeError, match='contains private foods'):
            migration.downgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM custom_foods')) == 1
        # Only fixture data in the transaction-local schema is removed to test clean downgrade.
        conn.execute(sa.text("DELETE FROM custom_foods WHERE id='private'"))
        migration.downgrade()
        assert conn.scalar(sa.text('SELECT calories FROM meal_items')) == 123.4
        migration.upgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM custom_foods')) == 0

    await connection.run_sync(verify)
