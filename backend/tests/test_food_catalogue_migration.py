import importlib.util
from pathlib import Path
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.services.food_catalog_v1 import catalog_entries,sync_catalog,catalog_id


def fixture(conn):
    schema='food_catalog_'+uuid4().hex
    conn.execute(sa.text(f'CREATE SCHEMA {schema}'))
    conn.execute(sa.text(f'SET LOCAL search_path TO {schema},public'))
    conn.execute(sa.text('CREATE TABLE foods (LIKE public.foods INCLUDING ALL)'))
    for col in ('browse_category','browse_aliases','source_info'):conn.execute(sa.text(f'ALTER TABLE foods DROP COLUMN {col}'))
    for name in ('meal_items','food_aliases'):conn.execute(sa.text(f'CREATE TABLE {name} (id varchar PRIMARY KEY, food_id varchar)'))
    for name,column in [('agent_proposals','payload_data'),('agent_artifacts','payload_data'),('agent_messages','content_data')]:
        conn.execute(sa.text(f'CREATE TABLE {name} (id varchar PRIMARY KEY,{column} jsonb)'))
    spec=importlib.util.spec_from_file_location('food_migration',Path(__file__).parents[1]/'alembic/versions/0031_expand_food_library.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.op=Operations(MigrationContext.configure(conn))
    return m


@pytest.mark.asyncio
async def test_food_migration_roundtrip_preserves_legacy_and_is_idempotent(db_session):
    def verify(conn):
        m=fixture(conn)
        conn.execute(sa.text("INSERT INTO foods (id,name_zh,category,calories_per_100g,protein_g,carbs_g,fat_g,is_active,is_common_in_china) VALUES ('legacy','豆腐','蛋白质',76,8,2,4,true,true)"))
        before=dict(conn.execute(sa.text("SELECT * FROM foods WHERE id='legacy'")).mappings().one())
        m.upgrade();assert conn.scalar(sa.text('SELECT count(*) FROM foods'))==60
        assert sync_catalog(conn)==0
        after=dict(conn.execute(sa.text("SELECT * FROM foods WHERE id='legacy'")).mappings().one())
        assert all(after[k]==v for k,v in before.items()) and after['browse_category']=='豆类豆制品'
        m.downgrade()
        assert dict(conn.execute(sa.text('SELECT * FROM foods')).mappings().one())==before
        m.upgrade();assert conn.scalar(sa.text('SELECT count(*) FROM foods'))==60
        m.downgrade()
    await (await db_session.connection()).run_sync(verify)


@pytest.mark.parametrize('kind',['nutrient','metadata','meal','proposal','untracked'])
@pytest.mark.asyncio
async def test_food_downgrade_refuses_data_loss(db_session,kind):
    def verify(conn):
        m=fixture(conn);m.upgrade();food_id=catalog_id('N01')
        if kind=='nutrient':conn.execute(sa.text('UPDATE foods SET calories_per_100g=999 WHERE id=:id'),{'id':food_id})
        elif kind=='metadata':conn.execute(sa.text("UPDATE foods SET browse_category='changed' WHERE id=:id"),{'id':food_id})
        elif kind=='meal':conn.execute(sa.text("INSERT INTO meal_items VALUES ('item',:id)"),{'id':food_id})
        elif kind=='proposal':conn.execute(sa.text("INSERT INTO agent_proposals VALUES ('proposal',jsonb_build_object('food_id',CAST(:id AS text)))"),{'id':food_id})
        else:conn.execute(sa.text("INSERT INTO foods(id,name_zh,category,browse_category,calories_per_100g,protein_g,carbs_g,fat_g,is_active,is_common_in_china) VALUES ('untracked','管理员新增','水果','水果',1,0,0,0,true,true)"))
        with pytest.raises(RuntimeError,match='refused'):m.downgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM food_catalog_imports'))==59
        assert conn.scalar(sa.text('SELECT count(*) FROM foods WHERE id=:id'),{'id':food_id})==1
    await (await db_session.connection()).run_sync(verify)


@pytest.mark.asyncio
async def test_food_import_conflict_rolls_back(db_session):
    def verify(conn):
        m=fixture(conn);entry=next(e for e in catalog_entries() if e['new'])
        conn.execute(sa.text("INSERT INTO foods(id,name_zh,category,calories_per_100g,protein_g,carbs_g,fat_g,is_active,is_common_in_china) VALUES (:id,:name,'碳水',999,0,0,0,true,true)"),{'id':'conflict','name':entry['fields']['name_zh']})
        with pytest.raises(RuntimeError,match='conflict'):
            with conn.begin_nested():m.upgrade()
        assert conn.scalar(sa.text('SELECT count(*) FROM foods'))==1
        assert conn.scalar(sa.text('SELECT calories_per_100g FROM foods'))==999
    await (await db_session.connection()).run_sync(verify)
