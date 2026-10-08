"""Regressions for old-task resumption and calendar-only query revisions."""
from unittest.mock import AsyncMock
from datetime import date

import pytest
from app.config import settings
from app.schemas.agent_task import ConversationTask,TaskSnapshot,TaskUpdate
from app.services import agent_intent_model as model
from app.services.agent_intent import IntentResolution,resolve_intent
from app.services.agent_query_reports import explicit_history_range,select_query_report

def context():
    state=TaskSnapshot(active_task_id='query',tasks=[
        ConversationTask(id='query',last_run_id='query-run',request='查询已完成训练',phase='responded'),
        ConversationTask(id='meal',last_run_id='meal-run',request='早餐提案',phase='awaiting_confirmation')])
    return [{'role':'task_state','content':state.model_dump_json()},{'role':'assistant','content':'已列出',
        'query_context':{'source_run_id':'query-run','primary_intent':'workout_history_query','resolved_query':'查询2026年9月17日已完成训练',
            'request_kind':'query','requested_effect':'read','risk_level':'low','successful_query':True}}]

def query_resolution(message,update=None):
    return IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',
        requested_effect='read',resolved_query=message,evidence_requirements=['workout_history'],confidence=.95,task_update=update)

@pytest.mark.asyncio
async def test_explicit_return_normalizes_continue_without_new_model_call(monkeypatch):
    message='回到未确认的早餐提案，看看当前内容'
    route=model.IntentRouteDecision(intent_domain='nutrition',request_kind='query',requested_effect='read',
        requested_output='answer',read_targets=[],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.9,
        task_update=TaskUpdate(action='continue',task_id='meal',trigger=message))
    invoked=AsyncMock(return_value=route);monkeypatch.setattr(model,'_invoke_model_route',invoked)
    result=await model._invoke_model_intent(message,context_messages=context())
    assert result.resolution.task_update.action=='resume'
    assert result.resolution.task_update.task_id=='meal'
    assert route.task_update.action=='continue'  # Preserve raw model evidence.
    assert invoked.await_count==1

@pytest.mark.asyncio
@pytest.mark.parametrize('message,target,phase,trigger',[
    ('继续改一下','meal','awaiting_confirmation',None),
    ('不要回到早餐提案','meal','awaiting_confirmation',None),
    ('不要回到早餐提案','meal','awaiting_confirmation','回到早餐提案'),
    ('如果回到早餐提案会怎样','meal','awaiting_confirmation','回到早餐提案'),
    ('如果回到早餐提案会怎样','meal','awaiting_confirmation',None),
    ('助手说回到早餐提案','meal','awaiting_confirmation',None),
    ('回到早餐提案','missing','awaiting_confirmation',None),
    ('回到早餐提案','meal','cancelled',None),
    ('继续改一下','meal','awaiting_confirmation','回到早餐提案'),
])
async def test_implicit_negated_unknown_cancelled_and_unattributed_return_still_rejected(monkeypatch,message,target,phase,trigger):
    history=context();state=TaskSnapshot.model_validate_json(history[0]['content']);state.tasks[1].phase=phase;history[0]['content']=state.model_dump_json()
    route=model.IntentRouteDecision(intent_domain='general',request_kind='query',requested_effect='read',requested_output='answer',read_targets=[],
        decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.9,
        task_update=TaskUpdate(action='continue',task_id=target,trigger=trigger or message))
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=route))
    with pytest.raises(model.IntentStructuredOutputError):await model._invoke_model_intent(message,context_messages=history)

@pytest.mark.parametrize('message,kind',[
    ('只查2026年9月17日已完成的训练，列出记录。','query'),
    ('列出记录后把卧推改为4组','mutation'),
    ('列出记录并删除昨天的训练','mutation'),
    ('把训练记录下来','mutation'),
    ('标记已完成','mutation'),
])
def test_nominal_listing_does_not_hide_real_writes(message,kind):
    assert resolve_intent(message).request_kind==kind

@pytest.mark.asyncio
@pytest.mark.parametrize('message,has_context,allowed',[
    ('改为9月12日至14日，年份仍是2026年，还是只要已完成。',True,True),
    ('日期范围改为2026-09-12至2026-09-14，仍只列已完成。',True,True),
    ('改为9月12日至14日，年份仍是2026年，还是只要已完成。',False,False),
    ('把训练记录的日期改为2026年9月12日',True,False),
    ('改为9月12日至14日，并删除昨天记录',True,False),
    ('改为9月12日至14日，并把卧推改为4组',True,False),
    ('改为9月12日至14日，然后标记已完成',True,False),
    ('改为9月12日至14日并保存',True,False),
])
async def test_read_scope_exception_requires_verified_query_and_no_write(monkeypatch,message,has_context,allowed):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic')
    update=TaskUpdate(action='continue',task_id='query',trigger=message)
    candidate=query_resolution('查询2026年9月12日至2026年9月14日已完成训练记录',update)
    monkeypatch.setattr(model,'_invoke_model_intent',AsyncMock(return_value=candidate))
    result=await model.resolve_intent_with_fallback(message,context_messages=context() if has_context else [],use_model=True)
    assert result.understanding_failed is not allowed

@pytest.mark.parametrize('query,expected',[
    ('查2026年9月17日已完成训练',(date(2026,9,17),date(2026,9,17))),
    ('查2026-09-17的记录',(date(2026,9,17),date(2026,9,17))),
    ('查2026年2月30日训练',None),
    ('查2026年9月12日至14日训练',None),  # Do not silently truncate a shorthand range.
    ('查2026-09-12至14日训练',None),
    ('查2026年9月12日起的训练',None),
    ('查2026年9月12日后的训练',None),
    ('查从2026-09-12的训练',None),
])
def test_single_day_calendar_scope_does_not_swallow_partial_ranges(query,expected):
    assert explicit_history_range(query)==expected

def test_single_day_gets_fact_only_report_but_advice_still_separate():
    report=select_query_report('查询2026年9月22日已完成训练记录',['workout.list_history'])
    assert report and report.kind=='calendar_history' and report.arguments['completed_only']
    assert select_query_report('查询2026年9月22日并推荐下一次安排',['workout.list_history']) is None

@pytest.mark.parametrize('bodies,ids',[
    ([{'proposal':{'id':'old'}},{'proposal':None}],[]),
    ([{'proposal':{'id':'old'}},{'proposal':{'id':'old'}}],[]),
    ([{'proposal':{'id':'old'}},{'cards':[]},{'proposal':{'id':'new'}}],['old']),
    ([{'proposal':{'id':'old'}},{'proposal':{'id':'mid'}},{'proposal':{'id':'new'}}],['old','mid']),
])
def test_evaluator_never_confirms_old_draft_when_replacement_is_missing(bodies,ids):
    from evals.proposal_lifecycle_checks import replaced_draft_references
    assert [r['id'] for r in replaced_draft_references(bodies)]==ids

@pytest.mark.asyncio
async def test_original_f03_provider_payload_resumes_and_preserves_pending_meal(monkeypatch):
    import json
    from pathlib import Path
    from app.services.agent_intent import ChangeRequest
    sample=json.loads((Path(__file__).parent/'fixtures/round13_f03_intent.json').read_text(encoding='utf-8'))
    raw=sample['route'];route=model.IntentRouteDecision.model_validate({**raw,'decision_action':None,'artifact_action':None})
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=route))
    extraction=IntentResolution(primary_intent='nutrition_today_query',intent_domain='nutrition',request_kind='mutation',requested_effect='create',
        resolved_query=raw['normalized_request'],confidence=.95,change_requests=[ChangeRequest(resource='nutrition',operation='create',field_path='meal',
            value={'meal_type':'午餐','items':[{'food_name':'米饭','amount_g':120},{'food_name':'鸡胸','amount_g':80}]})])
    extract=AsyncMock(return_value=extraction);monkeypatch.setattr(model,'_invoke_model_change_extraction',extract)
    result=await model._invoke_model_intent(sample['message'],context_messages=[{'role':'task_state','content':json.dumps(sample['state'],ensure_ascii=False)}])
    assert result.resolution.task_update.action=='resume'
    meal=result.resolution.change_requests[0].value
    assert meal['logged_at']=='2026-09-24' and meal['meal_type']=='午餐'
    assert {x['food_name']:x['amount_g'] for x in meal['items']}=={'米饭':120,'鸡胸':80}
    assert extract.await_args.kwargs['route'].task_update.action=='resume'
