"""Database-only prerequisite setup; invocation receives no model credentials."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

assert os.environ['DATABASE_URL'].split('@', 1)[-1] == '127.0.0.1:5432/rest_acceptance_formal_test'
assert not os.environ.get('DEEPSEEK_API_KEY')
assert not os.environ.get('EMBEDDING_API_KEY')
for command in [['python', '-m', 'alembic', 'upgrade', 'head'], ['python', 'scripts/seed_foods.py']]:
    subprocess.run(command, check=True)


async def verify():
    from sqlalchemy import func, select
    from app.database import AsyncSessionLocal, engine
    from app.models.agent import AgentRun, AgentProposal
    from app.models.model_budget import ModelCharge
    from scripts.evaluate_daily_meal_generation_real import _validate_evaluation_prerequisites, SYNONYMS
    from scripts.evaluate_agent_routing_real import CASES
    async with AsyncSessionLocal() as db:
        for model in [AgentRun, AgentProposal, ModelCharge]:
            assert await db.scalar(select(func.count(model.id))) == 0
        food = await _validate_evaluation_prerequisites(db)
    await engine.dispose()
    result = {'route_cases': len(CASES), 'meal_cases_per_pass': 10 + len(SYNONYMS),
              'food_prerequisites': food, 'model_http_calls': 0, 'credentials_passed': False}
    Path('/out/formal-prerequisites.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


asyncio.run(verify())
