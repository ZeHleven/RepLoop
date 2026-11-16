"""Read-only offline attribution of the newly discovered X01 first-turn gap."""
import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services import agent_intent_model as model
from app.services.agent_intent import resolve_intent


async def main(label):
    out = Path('/out')
    raw = json.loads((out / 'live-c2-extra/result.json').read_text())
    task = raw['tasks'][0]
    turn = task['turns'][0]
    routes = []
    for call in raw['provider_calls']:
        if call['run_id'] == turn['run_id']:
            for tool in call['response']['choices'][0]['message'].get('tool_calls', []):
                if tool['function']['name'] == 'submit_semantic_route':
                    payload = json.loads(tool['function']['arguments'])
                    routes.append(model.IntentRouteDecision.model_validate({**payload, 'decision_action': None, 'artifact_action': None}))
    with patch.object(settings, 'DEEPSEEK_API_KEY', 'synthetic-replay-only'), patch.object(model, '_invoke_model_route', AsyncMock(side_effect=routes)):
        result = await model.resolve_intent_with_fallback(turn['message'], context_messages=[], use_model=True)
    record = {'label': label, 'source': 'live-c2-extra/result.json serial-X01 first turn', 'mode': 'offline_recorded_provider_route_replay',
        'new_provider_requests': 0, 'direct_rule_kind': resolve_intent(turn['message']).request_kind,
        'understanding_failed': result.understanding_failed, 'error_category': result.error_category, 'attempt_count': result.attempt_count}
    (out / f'extra-replay-{label}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('label', choices=('baseline', 'candidate'))
    asyncio.run(main(parser.parse_args().label))
