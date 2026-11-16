"""Frozen 0031 catalogue. New releases must not rewrite this migration input."""
import json
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import sqlalchemy as sa


@lru_cache
def catalog_entries():
    return json.loads((Path(__file__).resolve().parents[1] / 'data/food_catalog_v1.json').read_text(encoding='utf-8'))


def catalog_id(key):
    return str(uuid5(NAMESPACE_URL, 'fitness-agent/food-catalog/v1/' + key))


def sync_catalog(bind):
    bind.execute(sa.text('SELECT pg_advisory_xact_lock(5031001)'))
    meta=sa.MetaData()
    foods=sa.Table('foods',meta,autoload_with=bind)
    ledger=sa.Table('food_catalog_imports',meta,autoload_with=bind)
    inserted=0
    for entry in catalog_entries():
        tracked=bind.execute(sa.select(ledger).where(ledger.c.catalog_key==entry['key'])).mappings().first()
        if tracked:
            if not bind.scalar(sa.select(foods.c.id).where(foods.c.id==tracked['food_id'])):
                raise RuntimeError('0031 catalogue ledger references missing food')
            continue
        if not entry['new']:
            rows=bind.execute(sa.select(foods).where(foods.c.name_zh==entry['name'])).mappings().all()
            if not rows: continue  # Seed may run after migrations on an empty installation.
            if len(rows)!=1: raise RuntimeError('0031 duplicate legacy food name; review before importing')
            row=rows[0]
            fields={k:entry[k] for k in ('browse_category','browse_aliases')}
            if any(row[k] not in (None,v) for k,v in fields.items()):
                raise RuntimeError('0031 legacy browse metadata conflict')
            bind.execute(foods.update().where(foods.c.id==row['id']).values(**fields))
            food_id,created=row['id'],False
        else:
            fields=entry['fields']
            rows=bind.execute(sa.select(foods).where(sa.or_(foods.c.name_zh==fields['name_zh'],foods.c.id==catalog_id(entry['key'])))).mappings().all()
            if rows:
                if len(rows)!=1 or any(rows[0][k]!=v for k,v in fields.items()):
                    raise RuntimeError(f"0031 food definition conflict: {fields['name_zh']}")
                food_id,created=rows[0]['id'],False
            else:
                food_id,created=catalog_id(entry['key']),True
                bind.execute(foods.insert().values(id=food_id,**fields));inserted+=1
        snapshot=dict(bind.execute(sa.select(foods).where(foods.c.id==food_id)).mappings().one())
        bind.execute(ledger.insert().values(catalog_key=entry['key'],food_id=food_id,created_by_import=created,initial_snapshot=snapshot))
    return inserted


def remove_imported_catalog(bind):
    bind.execute(sa.text('SELECT pg_advisory_xact_lock(5031001)'))
    meta=sa.MetaData();foods=sa.Table('foods',meta,autoload_with=bind);ledger=sa.Table('food_catalog_imports',meta,autoload_with=bind)
    entries=bind.execute(sa.select(ledger)).mappings().all();ids=[]
    tracked={r['food_id']:r for r in entries}
    # Dropping metadata columns must not silently discard later user/admin changes.
    for row in bind.execute(sa.select(foods)).mappings():
        original=tracked.get(row['id'])
        for key in ('browse_category','browse_aliases','source_info'):
            if row[key]!=(original['initial_snapshot'][key] if original else None):
                raise RuntimeError('0031 browse/source data changed; downgrade refused')
    for row in entries:
        if not row['created_by_import']: continue
        current=bind.execute(sa.select(foods).where(foods.c.id==row['food_id'])).mappings().first()
        if current is None or dict(current)!=row['initial_snapshot']:
            raise RuntimeError('0031 imported food changed; downgrade refused')
        for table_name,column in [('meal_items','food_id'),('food_aliases','food_id')]:
            table=sa.Table(table_name,meta,autoload_with=bind)
            if bind.scalar(sa.select(table.c.id).where(table.c[column]==row['food_id']).limit(1)):
                raise RuntimeError('0031 imported food referenced; downgrade refused')
        for table_name,column in [('agent_proposals','payload_data'),('agent_artifacts','payload_data'),('agent_messages','content_data')]:
            table=sa.Table(table_name,meta,autoload_with=bind)
            if bind.scalar(sa.select(table.c.id).where(sa.cast(table.c[column],sa.Text).contains(row['food_id'],autoescape=True)).limit(1)):
                raise RuntimeError('0031 imported food referenced by Agent data; downgrade refused')
        ids.append(row['food_id'])
    bind.execute(ledger.delete())
    if ids: bind.execute(foods.delete().where(foods.c.id.in_(ids)))
