"""Prerecorded obligations, real graph budget boundaries and exact comparisons."""
import asyncio,json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from pydantic import BaseModel
from app.config import settings
from app.services import agent_runtime as runtime,nutrition_history_reporting as nutrition
from app.services.agent_intent import IntentResolution
from app.services.agent_tools import NoArguments
from app.schemas.nutrition_history import NutritionHistoryPlan

class HistoryArgs(BaseModel):
    start_date:str=''
    end_date:str=''

class Model(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs):return self

def resolution(**kwargs):
    return IntentResolution(**dict(dict(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='assessment',requested_effect='read',risk_level='low',confidence=.99,resolved_query='评估昨天的记录',evidence_requirements=['profile_summary','nutrition_recent_context','nutrition_history']),**kwargs))

def install(monkeypatch,*,fail=None,responses=None):
    called=[]
    def build(db,*,user_id,allowlist):
        result=[]
        for tool_id in allowlist:
            async def invoke(_id=tool_id,**args):
                called.append((_id,args))
                if fail and _id==fail[0]:raise fail[1]
                return {'found':True,'user_id':user_id,'evidence':_id}
            tool=StructuredTool.from_function(coroutine=invoke,name=tool_id.replace('.','_'),description='Synthetic registered read',args_schema=HistoryArgs if tool_id.endswith('list_history') else NoArguments)
            result.append(tool)
        return result
    monkeypatch.setattr(runtime,'build_read_tools',build)
    monkeypatch.setattr(runtime,'_build_model',lambda:Model(responses=responses or [AIMessage(content='已按工具证据回答')]))
    return called

async def invoke(r,allow=None):
    return await runtime.invoke_langchain_agent(None,user_id='synthetic-owner',history=[],user_message='评估昨天的记录',resolved_query=r.resolved_query,tool_allowlist=allow or ['profile.get_summary','nutrition.get_recent_context','nutrition.list_history'],resolution=r)

@pytest.mark.asyncio
async def test_required_no_argument_reads_execute_before_model_and_are_traced(monkeypatch):
    calls=install(monkeypatch)
    result=await invoke(resolution())
    assert [x[0] for x in calls]==['profile.get_summary','nutrition.get_recent_context']
    assert all(x[1]=={} for x in calls)
    records=runtime._tool_call_records(result,'test')
    assert len(records)==2 and all(x.status=='completed' for x in records)
    assert all(x.arguments_data=={} for x in records)
    assert all(json.loads(m.content)['user_id']=='synthetic-owner' for m in result['messages'] if isinstance(m,ToolMessage))

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,effect',[('query','read'),('mutation','read'),('generation','read')])
async def test_prefetch_does_not_expand_non_assessment_authority(monkeypatch,kind,effect):
    calls=install(monkeypatch)
    await invoke(resolution(request_kind=kind,requested_effect=effect))
    assert calls==[]

@pytest.mark.asyncio
async def test_required_tool_missing_authority_never_executes(monkeypatch):
    calls=install(monkeypatch)
    result=await invoke(resolution(),['nutrition.list_history'])
    assert calls==[] and result.get('read_error_code')=='read_tool_permission_denied'

@pytest.mark.asyncio
@pytest.mark.parametrize('error,attempts,code',[(TimeoutError(),2,'read_tool_timeout'),(PermissionError(),1,'read_tool_permission_denied'),(RuntimeError(),1,'read_tool_execution_failed')])
async def test_prefetch_failure_retains_each_attempt_and_stops(monkeypatch,error,attempts,code):
    calls=install(monkeypatch,fail=('profile.get_summary',error))
    result=await invoke(resolution())
    assert len(calls)==attempts and result.get('read_error_code')==code
    assert len(runtime._tool_call_records(result,'test'))==attempts
    assert all(m.status=='error' for m in result['messages'] if isinstance(m,ToolMessage))

@pytest.mark.asyncio
async def test_retry_reserves_budget_for_other_obligations(monkeypatch):
    monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',3)
    calls=install(monkeypatch,fail=('profile.get_summary',TimeoutError()))
    result=await invoke(resolution())
    assert len(calls)==1 and result.get('read_error_code')=='read_tool_timeout'

@pytest.mark.asyncio
@pytest.mark.parametrize('error',[asyncio.CancelledError(),runtime.AgentRunOwnershipLost('lost')])
async def test_cancellation_and_ownership_are_never_retried_or_swallowed(monkeypatch,error):
    calls=install(monkeypatch,fail=('profile.get_summary',error))
    with pytest.raises(type(error)):await invoke(resolution())
    assert len(calls)==1

@pytest.mark.asyncio
async def test_parameterized_history_keeps_model_dates_and_shared_call_budget(monkeypatch):
    args={'start_date':'2026-10-08','end_date':'2026-10-08'}
    response=AIMessage(content='',tool_calls=[{'name':'nutrition_list_history','id':'requested-history','args':args}])
    calls=install(monkeypatch,responses=[response,AIMessage(content='按指定日期汇总')])
    result=await invoke(resolution())
    assert calls==[('profile.get_summary',{}),('nutrition.get_recent_context',{}),('nutrition.list_history',args)]
    assert len(runtime._tool_call_records(result,'test'))==3

@pytest.mark.asyncio
async def test_parallel_model_batch_cannot_exceed_remaining_budget(monkeypatch):
    monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',3)
    response=AIMessage(content='',tool_calls=[{'name':'nutrition_list_history','id':'h'+str(i),'args':{}} for i in range(2)])
    calls=install(monkeypatch,responses=[response])
    result=await invoke(resolution())
    assert len(calls)==2 and result.get('read_error_code')=='read_tool_budget_exhausted'

PLAN=dict(supported=True,days=30,start_date='2026-10-08',end_date='2026-10-08',logged_dates=[],meal_types=['午餐'],metrics=['protein_g','carbs_g','fat_g'],operations=['list'])
TARGETS=[{'metric':'protein_g','value':30,'source_quote':'蛋白质30克'},{'metric':'carbs_g','value':40,'source_quote':'碳水40克'},{'metric':'fat_g','value':10,'source_quote':'脂肪10克'}]
MESSAGE='请只比较昨天已记录的午餐与我指定的这餐目标：蛋白质30克、碳水40克、脂肪10克。给出差额，别评估全天。'

@pytest.mark.asyncio
async def test_grounded_meal_target_plan_and_deterministic_differences(monkeypatch):
    completion=SimpleNamespace(payload={**PLAN,'comparison_targets':TARGETS},parse_error=None)
    monkeypatch.setattr(nutrition,'structured_chat_completion',AsyncMock(return_value=completion))
    plan=await nutrition.plan_nutrition_history(message=MESSAGE,resolved_query=MESSAGE,today=date(2026,10,9))
    data={'scope':{'selection':'calendar_range','start_date':'2026-10-08','end_date':'2026-10-08','as_of':'2026-10-09','meal_types':['午餐'],'returned_dates':1,'first_logged_date':'2026-10-08','last_logged_date':'2026-10-08'},'days':[{'date':'2026-10-08','total_protein_g':34,'total_carbs_g':28,'total_fat_g':4.6}]}
    answer=nutrition.render_history(data,metrics=plan.metrics,operations=plan.operations,comparison_targets=plan.comparison_targets)
    for text in ['高于目标 4 g','低于目标 12 g','低于目标 5.4 g','给定','全天']:assert text in answer
    assert '香蕉' not in answer and '已保存目标' not in answer

@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'comparison_targets':[]},{'comparison_targets':TARGETS[:1]},{'comparison_targets':[dict(TARGETS[0],value=31),*TARGETS[1:]]},{'meal_types':[],'comparison_targets':TARGETS},{'end_date':'2026-10-09','comparison_targets':TARGETS}])
async def test_comparison_cannot_drop_targets_invent_values_or_expand_scope(monkeypatch,change):
    monkeypatch.setattr(nutrition,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload={**PLAN,**change},parse_error=None)))
    with pytest.raises(nutrition.NutritionHistoryPlanError):
        await nutrition.plan_nutrition_history(message=MESSAGE,resolved_query=MESSAGE,today=date(2026,10,9))

@pytest.mark.parametrize('message',[
    '我的午餐目标蛋白质30克，但不要比较，只列昨天午餐的热量。',
    '不用把昨天午餐和蛋白质30克的目标比较，只查热量。',
    '不要核对蛋白质30克这个目标，昨天午餐只列热量。',
])
def test_explicitly_declined_comparison_does_not_force_target_path(message):
    plan=NutritionHistoryPlan.model_validate(dict(PLAN,metrics=['calories']))
    assert nutrition._comparison_is_grounded(message,plan)
