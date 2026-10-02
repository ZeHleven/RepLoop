import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.services import agent_intent_model as im
from app.services.ai_client import StructuredCompletionResult
from app.services.agent_change_validation import validate_semantic_changes

def replay():
    return json.loads((Path(__file__).parent/'fixtures/round23_partial_meal.json').read_text(encoding='utf-8'))

def completion(payload):
    return StructuredCompletionResult(payload=payload,raw_output=json.dumps(payload,ensure_ascii=False),mode='deepseek_json_mode',finish_reason='stop',duration_ms=0,output_chars=0)

@pytest.mark.asyncio
async def test_actual_empty_meal_extraction_requires_model_repair(monkeypatch):
    data=replay();monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(return_value=completion(data['extraction'])))
    with pytest.raises(im.IntentStructuredOutputError,match='meal_partial_structure_missing'):
        await im._invoke_model_change_extraction(data['message'],route=im.IntentRouteDecision.model_validate(data['route']))

@pytest.mark.asyncio
@pytest.mark.parametrize('oil',[None,2])
async def test_partial_or_complete_meal_keeps_known_fields(monkeypatch,oil):
    data=replay();value={'logged_at':'2026-10-01','meal_type':'午餐','items':[{'food_name':'米饭','amount_g':160},{'food_name':'鸡胸','amount_g':80},{'food_name':'橄榄油','amount_g':oil}]}
    payload={**data['extraction'],'change_requests':[{'resource':'nutrition','operation':'create','field_path':'meal','target_reference':None,'value_json':json.dumps(value,ensure_ascii=False),'preserve_unspecified':True}]}
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(return_value=completion(payload)))
    result,_=await im._invoke_model_change_extraction(data['message'],route=im.IntentRouteDecision.model_validate(data['route']))
    assert result.change_requests[0].value==value
    valid=validate_semantic_changes(intent_domain='nutrition',request_kind='mutation',requested_effect='create',change_requests=result.change_requests)
    assert valid.complete is (oil is not None)
    assert valid.clarification_question==('请补充橄榄油的克数。' if oil is None else None)
