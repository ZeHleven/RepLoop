"""Frozen v1 catalogue/import rules, also used by migration 0030. Add v2, don't rewrite v1."""
import json
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa


@lru_cache
def catalog_entries():
    return json.loads((Path(__file__).resolve().parents[1] / 'data/exercise_catalog_v1.json').read_text(encoding='utf-8'))


def catalog_id(key):
    return str(uuid5(NAMESPACE_URL, 'fitness-agent/exercise-catalog/v1/' + key))


def sync_catalog(bind):
    """One transaction, exact-definition reuse only; never overwrite/activate existing exercises."""
    bind.execute(sa.text('SELECT pg_advisory_xact_lock(5030001)'))
    metadata = sa.MetaData()
    exercises = sa.Table('exercises', metadata, autoload_with=bind)
    ledger = sa.Table('exercise_catalog_imports', metadata, autoload_with=bind)
    inserted = 0
    for entry in catalog_entries():
        if not entry['new']:
            continue
        fields = entry['fields']
        tracked = bind.execute(sa.select(ledger).where(ledger.c.catalog_key == entry['key'])).mappings().first()
        if tracked:
            # A repeat invocation never overwrites later edits or changes identity.
            if not bind.scalar(sa.select(exercises.c.id).where(exercises.c.id == tracked['exercise_id'])):
                raise RuntimeError('Catalogue ledger references a missing exercise')
            continue
        candidates = bind.execute(sa.select(exercises).where(
            exercises.c.owner_id.is_(None), sa.or_(
                sa.func.lower(exercises.c.name_en) == fields['name_en'].lower(),
                exercises.c.name_zh == fields['name_zh'],
                exercises.c.id == catalog_id(entry['key']),
            ),
        )).mappings().all()
        if candidates:
            if len(candidates) != 1 or any(candidates[0][key] != value for key, value in fields.items()):
                raise RuntimeError(f"0030 catalogue conflict: {fields['name_zh']}; review existing public definition before migrating")
            exercise_id, created = candidates[0]['id'], False
        else:
            exercise_id, created = catalog_id(entry['key']), True
            bind.execute(exercises.insert().values(id=exercise_id, owner_id=None, **fields))
            inserted += 1
        snapshot = dict(bind.execute(sa.select(exercises).where(exercises.c.id == exercise_id)).mappings().one())
        bind.execute(ledger.insert().values(catalog_key=entry['key'], exercise_id=exercise_id,
                                           created_by_import=created, initial_snapshot=snapshot))
    return inserted


def remove_imported_catalog(bind):
    """Reject loss of referenced or changed records. Previously existing matches are never deleted."""
    bind.execute(sa.text('SELECT pg_advisory_xact_lock(5030001)'))
    metadata = sa.MetaData()
    exercises = sa.Table('exercises', metadata, autoload_with=bind)
    ledger = sa.Table('exercise_catalog_imports', metadata, autoload_with=bind)
    entries = bind.execute(sa.select(ledger).where(ledger.c.created_by_import.is_(True))).mappings().all()
    ids = [row['exercise_id'] for row in entries]
    for row in entries:
        current = bind.execute(sa.select(exercises).where(exercises.c.id == row['exercise_id'])).mappings().first()
        if current is None or dict(current) != row['initial_snapshot']:
            raise RuntimeError('0030 imported exercise was changed; refusing destructive downgrade')
        for table_name in ('planned_exercises', 'session_exercises'):
            table = sa.Table(table_name, metadata, autoload_with=bind)
            if bind.scalar(sa.select(table.c.id).where(table.c.exercise_id == row['exercise_id']).limit(1)):
                raise RuntimeError('0030 imported exercises are referenced by plans/history; downgrade refused')
        # Proposals/artifacts/messages may embed IDs in JSON rather than FK columns.
        for table_name, column in (('agent_proposals', 'payload_data'), ('agent_artifacts', 'payload_data'), ('agent_messages', 'content_data')):
            table = sa.Table(table_name, metadata, autoload_with=bind)
            if column in table.c and bind.scalar(sa.select(table.c.id).where(
                sa.cast(table.c[column], sa.Text).contains(row['exercise_id'], autoescape=True),
            ).limit(1)):
                raise RuntimeError('0030 imported exercises are referenced by Agent data; downgrade refused')
    bind.execute(ledger.delete())
    if ids:
        bind.execute(exercises.delete().where(exercises.c.id.in_(ids)))
