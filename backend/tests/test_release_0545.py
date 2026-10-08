"""Release identifiers and data packaging must move together."""
import json
from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[2]


def test_backend_and_miniapp_versions_match():
    package = json.loads((ROOT / 'miniapp/package.json').read_text(encoding='utf-8'))
    assert app.version == package['version'] == '0.5.46'


def test_backend_packaging_requires_catalogues_migrations_and_agent_runtime():
    script = (ROOT / 'scripts/package_cloudbase_backend.ps1').read_text(encoding='utf-8')
    assert '[string]$Version = "0.5.46"' in script
    requirements = script.split('$required = @(', 1)[1].split('\n)', 1)[0]
    for relative in (
        'alembic/versions/0032_agent_queue_position.py',
        'app/schemas/agent_task.py',
        'app/services/agent_evidence_contract.py',
        'app/services/agent_plan_completeness.py',
        'app/services/agent_plan_quantities.py',
        'app/services/agent_run_order.py',
        'app/services/agent_task_state.py',
        'app/services/business_clock.py',
        'app/services/history_status_scope.py',
        'app/services/structured_schema.py',
        'alembic/versions/0031_expand_food_library.py',
        'app/data/food_catalog_v1.json',
        'app/data/FOOD-DATA-NOTICES.md',
        'app/services/food_catalog_v1.py',
        'app/services/food_library.py',
        'alembic/versions/0030_expand_strength_exercises.py',
        'app/data/exercise_catalog_v1.json',
        'app/services/exercise_catalog_v1.py',
        'app/services/exercise_search.py',
    ):
        assert f'"./{relative}"' in requirements
        assert (ROOT / 'backend' / relative).is_file()


def test_runtime_catalogue_preserves_all_reviewed_nutrient_fields():
    runtime = json.loads((ROOT / 'backend/app/data/food_catalog_v1.json').read_text(encoding='utf-8'))
    reviewed = json.loads((ROOT / 'docs/food-source-audit/source-matrix-v3.json').read_text(encoding='utf-8'))
    imported = {entry['key']: entry['fields'] for entry in runtime if entry['new']}
    assert set(imported) == {entry['candidate'] for entry in reviewed['records']}
    for entry in reviewed['records']:
        actual = imported[entry['candidate']]
        assert actual['name_zh'] == entry['proposedName']
        for field, source_key in [('calories_per_100g', 'kcal'), ('protein_g', 'protein_g'),
                                  ('carbs_g', 'carbs_g'), ('fat_g', 'fat_g'), ('fiber_g', 'fiber_g')]:
            assert actual[field] == entry['nutrients'][source_key]
        assert actual['source_reference'] == entry['source']['url']
        assert actual['source_info']['provider'] == entry['provider']
