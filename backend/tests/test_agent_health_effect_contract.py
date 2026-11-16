import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.config import settings
from app.services import agent_intent_model as model
from app.services.ai_client import StructuredCompletionResult


def route(effect='update', domain='health', kind='mutation'):
    return dict(intent_domain=domain, request_kind=kind, requested_effect=effect,
                requested_output='answer', read_targets=[], decision_action='none',
                artifact_action='none', normalized_request='记录膝关节手术史',
                risk_level='medium', confidence=0.9)


@pytest.mark.parametrize('effect', ['create', 'delete'])
def test_health_writes_cannot_reach_unsupported_resource_operations(effect):
    with pytest.raises(ValidationError, match='health mutation requires update'):
        model.IntentRouteDecision.model_validate(route(effect))


@pytest.mark.parametrize('payload', [route(), route('read', kind='query'),
                                    route('read', kind='assessment'),
                                    route('create', domain='profile')])
def test_valid_health_reads_updates_and_weight_create_keep_their_effect(payload):
    result = model.IntentRouteDecision.model_validate(payload)
    assert result.requested_effect == payload['requested_effect']
    assert result.risk_level == payload['risk_level']


def completion(payload):
    raw = json.dumps(payload, ensure_ascii=False)
    return StructuredCompletionResult(payload=payload, raw_output=raw,
        mode='deepseek_strict_tool', finish_reason='tool_calls', duration_ms=1, output_chars=len(raw))


@pytest.mark.asyncio
async def test_invalid_health_create_is_repaired_before_extracting_changes(monkeypatch):
    calls = []
    replies = [completion(route('create')), completion(route()), completion({
        'change_requests': [{'resource': 'health', 'operation': 'update',
            'field_path': 'health.injuries', 'target_reference': None,
            'value_json': '["膝关节手术"]', 'preserve_unspecified': True}],
        'normalized_request': '记录膝关节手术史', 'confidence': 0.9,
    })]

    async def invoke(*args, **kwargs):
        calls.append(kwargs['function_name'])
        return replies.pop(0)

    monkeypatch.setattr(settings, 'DEEPSEEK_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(model, 'structured_chat_completion', invoke)
    result = await model.resolve_intent_with_fallback('请记录我接受过膝关节手术', use_model=True)
    assert calls == ['submit_semantic_route', 'submit_semantic_route', 'submit_domain_changes']
    assert result.source == 'model' and result.attempt_count == 2
    assert not result.understanding_failed
    assert result.resolution.requested_effect == 'update'
    assert result.resolution.change_requests[0].operation == 'update'
    assert result.resolution.change_requests[0].value == ['膝关节手术']
    assert result.resolution.risk_level == 'medium'
