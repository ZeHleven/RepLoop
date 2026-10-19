"""Original HM02 route and independent target/authority counterexamples."""
import json
from unittest.mock import AsyncMock
import pytest
from app.services import agent_intent_model as im
from app.services.ai_client import StructuredCompletionResult
from app.services.agent_meal_target import explicit_meal_creation,normalize_meal_artifact_target
from round80_support import DATA,MESSAGE,payload,bad_route,partial

def completion(p):
    text=json.dumps(p,ensure_ascii=False)
    return StructuredCompletionResult(payload=p,raw_output=text,mode='offline',finish_reason='tool_calls',duration_ms=0,output_chars=len(text))

@pytest.mark.asyncio
@pytest.mark.parametrize('artifacts',[0,1,2])
async def test_same_wrong_route_now_reaches_partial_meal_extraction(monkeypatch,artifacts):
    mock=AsyncMock(side_effect=[completion(bad_route()),completion(partial())]);monkeypatch.setattr(im,'structured_chat_completion',mock)
    result=await im._invoke_model_intent(MESSAGE,conversation_action_state={'active_daily_meal_artifact_count':artifacts,'pending_proposal_count':0},timeout_seconds=20)
    assert mock.await_count==2
    assert mock.await_args_list[1].kwargs['function_name']=='submit_domain_changes'
    request=mock.await_args_list[1].args[0][1]['content']
    assert '"artifact_action": null' in request and '"artifact_action": "save_as_proposal"' not in request
    change=result.resolution.change_requests[0]
    assert change.field_path=='meal' and change.value['logged_at']=='2026-10-06' and change.value['meal_type']=='晚餐'
    assert change.value['items']==[{'food_name':'土豆','amount_g':180},{'food_name':'鱼肉','amount_g':None}]

@pytest.mark.parametrize('message',[
    MESSAGE,'请记录昨天午餐鸡胸肉150克。','帮我补记10月6日早餐燕麦50克、牛奶200毫升，待确认。',
    '记下今晚晚餐土豆和鱼肉，先问缺的信息。','新增一条加餐记录：苹果100g。',
    '把昨天晚餐的鱼肉130g记下来，给待确认提案。',
    '记录晚餐鱼肉130g，不要直接确认。','不要保存全天方案，只记录晚餐鱼肉130克。',
])
def test_meal_target_from_source_not_specific_food_or_empty_artifact_count(message):
    route=im.IntentRouteDecision.model_validate(bad_route(message))
    assert explicit_meal_creation(message)
    corrected=normalize_meal_artifact_target(route,message)
    assert corrected.artifact_action is None and corrected.task_update==route.task_update
    assert corrected.normalized_request==route.normalized_request

@pytest.mark.parametrize('message',[
    '保存这份方案。','把刚才的饮食方案提交成待确认提案。','不要确认，只保存这份方案。',
    '把上面这份方案记录下来。','请保存它。',
])
def test_actual_artifact_save_stays_on_existing_path(message):
    route=im.IntentRouteDecision.model_validate(bad_route(message))
    assert not explicit_meal_creation(message)
    assert normalize_meal_artifact_target(route,message)==route

@pytest.mark.parametrize('message',[
    '如果记录晚餐鱼肉130克，应该怎么操作？','朋友说“记录晚餐鱼肉130克”。',
    '不要记录晚餐鱼肉130克。','我不想保存方案。','这个方案保存了吗？',
    '如何保存这个方案？',
])
def test_noncurrent_or_negative_statement_is_not_save_authority(message):
    route=im.IntentRouteDecision.model_validate(bad_route(message))
    assert not explicit_meal_creation(message)
    with pytest.raises(ValueError,match='artifact_save_requires_current_request'):normalize_meal_artifact_target(route,message)

@pytest.mark.parametrize('action',['save_as_proposal','none'])
@pytest.mark.parametrize('message',[
    '记录晚餐鱼肉130克；保存刚才的全天方案。',
    '记录晚餐鱼肉130克并保存这份方案。',
])
def test_mixed_targets_cannot_drop_one_goal_on_repair(message,action):
    raw=bad_route(message);raw['artifact_action']=action
    with pytest.raises(ValueError,match='separate_requests'):normalize_meal_artifact_target(im.IntentRouteDecision.model_validate(raw),message)

@pytest.mark.asyncio
@pytest.mark.parametrize('mixed',[False,True])
async def test_change_extractor_cannot_reintroduce_artifact_save(monkeypatch,mixed):
    extraction=partial();rogue=dict(resource='nutrition',operation='create',field_path='daily_meal_plan.save',target_reference='latest_active_artifact_in_conversation',value_json='null',preserve_unspecified=True)
    extraction['change_requests']=(extraction['change_requests'] if mixed else [])+[rogue]
    mock=AsyncMock(side_effect=[completion(bad_route()),completion(extraction)]);monkeypatch.setattr(im,'structured_chat_completion',mock)
    with pytest.raises(im.IntentStructuredOutputError,match='artifact_save_requires_artifact_action'):
        await im._invoke_model_intent(MESSAGE,timeout_seconds=20)
    assert mock.await_count==2

@pytest.mark.asyncio
async def test_transport_failure_does_not_invent_missing_grams(monkeypatch):
    mock=AsyncMock(side_effect=[completion(bad_route()),TimeoutError('synthetic extraction timeout')]);monkeypatch.setattr(im,'structured_chat_completion',mock)
    with pytest.raises(TimeoutError):await im._invoke_model_intent(MESSAGE,timeout_seconds=20)
    assert mock.await_count==2
