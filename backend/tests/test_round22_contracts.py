"""Source semantics regressions; originals are sealed round21 model outputs."""
import json
from pathlib import Path
import pytest
from app.schemas.agent_task import TaskSnapshot,TaskUpdate,ConversationTask
from app.services.agent_intent import ChangeRequest,IntentResolution
from app.services.agent_plan_completeness import _provided_value,enforce_plan_completeness
from app.services.agent_task_state import preserve_pending_plan_changes
from app.services.history_status_scope import parse_history_status_scope,history_status_problem,is_history_scope_revision

def fixtures():
    return json.loads((Path(__file__).parent/'fixtures/round21_contract_replays.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('name',['K01','K02'])
def test_same_numeric_response_now_fulfills_gap(name):
    row=fixtures()[name]
    changes=[ChangeRequest.model_validate(c) for c in row['changes']]
    state=TaskSnapshot.model_validate(row['state']);state.transition='continue'
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.95,change_requests=changes)
    result=enforce_plan_completeness(r,state,row['message'])
    assert not result.clarification_required
    assert not next(t for t in state.tasks if t.id==state.active_task_id).plan_input_gaps

def test_same_pending_completion_response_preserves_all_eight_fields():
    row=fixtures()['K04'];changes=[ChangeRequest.model_validate(c) for c in row['changes']]
    result=preserve_pending_plan_changes([{'role':'task_state','content':json.dumps(row['state'],ensure_ascii=False)}],TaskUpdate.model_validate(row['update']),changes,message=row['message'])
    assert len(result)==8
    assert {(c.target_reference,c.field_path,c.value) for c in result}=={(c.target_reference,c.field_path,c.value) for c in changes}

@pytest.mark.parametrize('name,expected',[
 ('K09',('completed','in_progress','ended_early')),('K10',('abandoned',)),('K11',('abandoned',))])
def test_same_status_response_agrees_with_source(name,expected):
    row=fixtures()[name]
    r=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',risk_level='low',confidence=.95,resolved_query=row['resolved_query'])
    assert parse_history_status_scope(row['message']).statuses==expected
    assert parse_history_status_scope(row['resolved_query']).statuses==expected
    assert not history_status_problem(r,row['message'])

@pytest.mark.parametrize('field,target,value,text,ok',[
 ('schedule.duration_weeks',None,6,'周期用六周',True),
 ('schedule.duration_weeks',None,16,'周期十六周',True),
 ('schedule.duration_weeks',None,6,'周期十六周',False),
 ('schedule.duration_weeks',None,6,'周期6到8周',False),
 ('schedule.duration_weeks',None,8,'周期6到8周',False),
 ('schedule.duration_weeks',None,6,'周期大约六周',False),
 ('schedule.duration_weeks',None,6,'周期不要六周',False),
 ('schedule.duration_weeks',None,6,'周期六周先不采用',False),
 ('schedule.duration_weeks',None,6,'不要六周，改成八周',False),
 ('schedule.duration_weeks',None,8,'不要六周，改成八周',True),
 ('schedule.duration_weeks',None,6,'旧计划六周，新周期待定',False),
 ('schedule.days_per_week',None,2,'每周两天',True),
 ('exercise.sets','深蹲',4,'深蹲四组',True),
 ('exercise.reps','深蹲','12','深蹲十二次',True),
 ('exercise.rest_seconds','深蹲',90,'深蹲每组之间休息一分半钟',True),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息一点五分钟',True),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息1.5分钟',True),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息一分三十秒',True),
 ('exercise.rest_seconds','深蹲',30,'深蹲休息半分钟',True),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息九十秒',True),
 ('exercise.rest_seconds','深蹲',105,'深蹲休息一百零五秒',True),
 ('exercise.rest_seconds','深蹲',90,'卧推休息90秒，深蹲不变',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息30秒，卧推休息90秒',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息1.5秒',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲90次',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息-90秒',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息190秒',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息90秒或120秒',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息90秒到120秒',False),
 ('exercise.rest_seconds','深蹲',90,'如果可以深蹲休息90秒',False),
 ('exercise.rest_seconds','深蹲',90,'深蹲休息90秒吗',False),
 ('exercise.recommended_weight_kg','深蹲',22.5,'深蹲重量二十二点五公斤',True),
 ('exercise.recommended_weight_kg','深蹲',22.5,'深蹲22500克',True),
 ('exercise.recommended_weight_kg','深蹲',22.5,'深蹲22.5kg',True),
 ('exercise.recommended_weight_kg','深蹲',22.5,'深蹲22.5磅',False),
 ('exercise.recommended_weight_kg','深蹲',22.5,'深蹲重量22.5公斤左右',False),
])
def test_quantity_value_requires_source_units_polarity_and_target(field,target,value,text,ok):
    c=ChangeRequest(resource='workout_plan',operation='update',field_path=field,target_reference=target,value=value)
    assert _provided_value(c,text) is ok

def pending_state():
    task=ConversationTask(id='p',request='当前训练计划',last_run_id='old',phase='awaiting_input',
        requirements=[{'key':'pending','quote':'深蹲休息待定','source_run_id':'old'},{'key':'sets','quote':'卧推四组','source_run_id':'old'}],
        plan_input_gaps=[{'field_path':'exercise.rest_seconds','target_reference':'深蹲','quote':'深蹲休息待定'}],
        unproposed_plan_changes=[{'field_path':'exercise.sets','target_reference':'卧推','value':4}])
    return TaskSnapshot(active_task_id='p',tasks=[task],transition='continue')

@pytest.mark.parametrize('key,target,value,message,valid',[
 ('pending','深蹲',90,'深蹲休息一分半钟',True),
 ('pending','卧推',90,'卧推休息一分半钟',False),
 ('pending','深蹲',120,'深蹲休息一分半钟',False),
 ('pending','深蹲',90,'撤回深蹲休息要求',False),
 ('sets','深蹲',90,'深蹲休息一分半钟',False),
 ('missing','深蹲',90,'深蹲休息一分半钟',False),
 ('pending','深蹲',90,'深蹲休息不要90秒',False),
])
def test_pending_fulfillment_never_authorizes_other_requirement_removal(key,target,value,message,valid):
    state=pending_state();update=TaskUpdate(action='continue',task_id='p',trigger=message,requirements=[{'key':key,'quote':message,'remove':True}])
    changes=[ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.rest_seconds',target_reference=target,value=value)]
    ctx=[{'role':'task_state','content':state.model_dump_json()}]
    if valid:
        merged=preserve_pending_plan_changes(ctx,update,changes,message=message)
        assert len(merged)==2 and any(c.field_path=='exercise.sets' and c.value==4 for c in merged)
    else:
        with pytest.raises(ValueError,match='withdrawal_fields_required'):
            preserve_pending_plan_changes(ctx,update,changes,message=message)

@pytest.mark.parametrize('message,expected',[
 ('跳过、放弃都不算，其他状态保留',('completed','in_progress','ended_early')),
 ('排除状态为跳过和放弃的场次，保留其他状态（含已完成、进行中、提前结束）',('completed','in_progress','ended_early')),
 ('把进行中也去掉，只查放弃',('abandoned',)),
 ('现在只看放弃，其他状态全部排除',('abandoned',)),
 ('只看进行中、提前结束，其余的状态一律排除',('in_progress','ended_early')),
 ('排除状态为“跳过”和“放弃”，其余都保留',('completed','in_progress','ended_early')),
 ('进行中也排除，只看提前结束',('ended_early',)),
 ('跳过不计入，其他状态都要',('completed','in_progress','ended_early','abandoned')),
 ('只查已完成和提前结束，改为仅查进行中和放弃',('in_progress','abandoned')),
 ('只查已完成和提前结束，改为只查进行中，排除放弃',('in_progress',)),
 ('只看已完成，排除已完成',None),
 ('不要排除跳过',None),('跳过也别去掉',None),('跳过不一定去掉',None),
 ('跳过可能不算',None),('跳过不是不算',None),
 ('只看放弃，其他状态不要全部排除',None),
 ('只看放弃，其他状态全部保留',None),
 ('跳过和放弃都不算，但只查放弃',None),
 ('排除其他状态',None),
])
def test_status_operations_and_unsupported_negation(message,expected):
    scope=parse_history_status_scope(message)
    assert scope.complete is (expected is not None)
    assert scope.statuses==expected

@pytest.mark.parametrize('message',[
 '把当前训练状态改为已完成',
 '日期不变，只查放弃，然后删除已完成的记录',
 '只看进行中，随后把它改为放弃',
])
def test_new_status_language_does_not_relax_write_guard(message):
    assert not is_history_scope_revision(message)


@pytest.mark.parametrize('index,expected',[(1,('in_progress','abandoned')),(2,('abandoned',))])
def test_live_k10_full_query_verb_response(index,expected):
    data=json.loads((Path(__file__).parent/'fixtures/round22_k10_status_variant.json').read_text(encoding='utf-8'))
    row=data['turns'][index]
    assert parse_history_status_scope(row['resolved_query']).statuses==expected
    assert parse_history_status_scope(row['message']).statuses==expected


@pytest.mark.parametrize('verb',['查','查询','查看'])
def test_status_query_verbs_share_polarity(verb):
    assert parse_history_status_scope('不再'+verb+'跳过，只看放弃').statuses==('abandoned',)
    assert parse_history_status_scope('跳过和放弃都不再'+verb+'，其他状态保留').statuses==('completed','in_progress','ended_early')
    assert not parse_history_status_scope('不是不再'+verb+'跳过').complete
    assert not parse_history_status_scope('只看放弃，不再'+verb+'放弃').complete


from unittest.mock import AsyncMock
from app.services import agent_intent_model as im
from app.services.ai_client import StructuredCompletionResult
from app.services.agent_task_state import normalize_explicit_task_return
from app.services.agent_plan_completeness import workflow_release

def added_fixtures():
    return json.loads((Path(__file__).parent/'fixtures/round22_additional_replays.json').read_text(encoding='utf-8'))

@pytest.mark.asyncio
@pytest.mark.parametrize('name,route_index,expected_sets',[('K02',2,4),('G05',3,3)])
async def test_live_raw_workflow_and_numeric_restore_replay(monkeypatch,name,route_index,expected_sets):
    row=added_fixtures()[name];state=TaskSnapshot.model_validate(row['turns'][0]['body']['execution_trace']['task_state'])
    if name=='G05':state.tasks[0].proposal_pending=True
    records=row['model_outputs'][route_index:route_index+2]
    assert [r['stage'] for r in records]==['submit_semantic_route','submit_domain_changes']
    completions=[StructuredCompletionResult(payload=r['payload'],raw_output=r['raw'],mode=r['mode'],finish_reason='stop',duration_ms=0,output_chars=len(r['raw'])) for r in records]
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(side_effect=completions))
    result=await im._invoke_model_intent(row['turns'][1]['message'],context_messages=[{'role':'task_state','content':state.model_dump_json()}],timeout_seconds=30)
    assert not result.resolution.clarification_required
    assert next(c.value for c in result.resolution.change_requests if c.field_path=='exercise.sets' and c.target_reference=='卧推')==expected_sets
    if name=='K02':
        assert len(result.resolution.change_requests)==8
    else:
        edit=next(e for e in result.resolution.task_update.requirements if e.key=='bench_sets')
        assert not edit.remove and edit.quote in row['turns'][1]['message']

@pytest.mark.parametrize('ambiguous',[False,True])
def test_live_draft_identity_uses_verified_type_not_summary_wording(ambiguous):
    row=added_fixtures()['G01'];state=TaskSnapshot.model_validate(row['turns'][1]['body']['execution_trace']['task_state'])
    route=TaskUpdate.model_validate(row['model_outputs'][3]['payload']['task_update'])
    task=next(t for t in state.tasks if t.id==route.task_id)
    assert task.proposal_pending and task.pending_plan_changes and '训练计划' not in task.request
    if ambiguous:
        twin=task.model_copy(deep=True);twin.id='other-plan';twin.proposal_id='other-proposal';state.tasks.append(twin)
    result=normalize_explicit_task_return([{'role':'task_state','content':state.model_dump_json()}],route,row['turns'][2]['message'])
    assert result.action==('continue' if ambiguous else 'resume')

@pytest.mark.parametrize('message,allowed',[
 ('生成提案',True),('请生成完整提案',True),('生成提案，先不要应用',True),
 ('不要生成提案',False),('稍后再生成提案',False),('等信息齐全后生成提案',False),
 ('生成提案前再核对周期',False),('如果齐全就生成提案',False),
])
def test_generation_instruction_releases_only_workflow(message,allowed):
    assert workflow_release('信息齐全后再生成提案',message) is allowed
    assert not workflow_release('卧推四组',message)

def test_short_generation_does_not_satisfy_missing_business_value():
    state=pending_state()
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.95)
    assert enforce_plan_completeness(r,state,'生成提案').clarification_required

def test_sourced_quote_cannot_hide_negated_generation():
    state=pending_state();state.tasks[0].requirements.append(__import__('app.schemas.agent_task',fromlist=['TaskRequirement']).TaskRequirement(key='wait',quote='信息齐全后再生成提案',source_run_id='old',kind='workflow'))
    update=TaskUpdate(action='continue',task_id='p',trigger='不要生成提案',requirements=[{'key':'wait','quote':'生成提案','remove':True}])
    with pytest.raises(ValueError,match='withdrawal_fields_required'):
        preserve_pending_plan_changes([{'role':'task_state','content':state.model_dump_json()}],update,[],message='不要生成提案')


@pytest.mark.parametrize('message,value,target,valid',[
 ('卧推组数恢复3组',3,'卧推',True),
 ('撤回卧推组数这一项修改，恢复原计划的3组',3,'卧推',True),
 ('撤回卧推组数这一项修改，恢复原计划的3组',5,'卧推',False),
 ('深蹲组数恢复3组',3,'深蹲',False),
 ('撤回卧推组数',3,'卧推',False),
 ('卧推组数恢复原计划',3,'卧推',False),
 ('不要把卧推组数恢复3组',3,'卧推',False),
 ('卧推次数恢复3次',3,'卧推',False),
])
def test_numeric_reset_needs_exact_source_and_existing_field(message,value,target,valid):
    from app.services.agent_task_state import normalize_sourced_plan_resets
    state=pending_state()
    update=TaskUpdate(action='continue',task_id='p',trigger=message,requirements=[{'key':'sets','quote':message,'remove':True}])
    changes=[ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.sets',target_reference=target,value=value)]
    ctx=[{'role':'task_state','content':state.model_dump_json()}]
    result=normalize_sourced_plan_resets(ctx,update,changes,message)
    assert result.requirements[0].remove is (not valid)
    if not valid:
        with pytest.raises(ValueError,match='withdrawal_fields_required'):
            preserve_pending_plan_changes(ctx,result,changes,message=message)


@pytest.mark.asyncio
async def test_live_conditional_generation_fragment_is_satisfied(monkeypatch):
    row=json.loads((Path(__file__).parent/'fixtures/round22_conditional_gap.json').read_text(encoding='utf-8'))
    state=TaskSnapshot.model_validate(row['turns'][0]['body']['execution_trace']['task_state'])
    completions=[StructuredCompletionResult(payload=r['payload'],raw_output=r['raw'],mode=r['mode'],finish_reason='stop',duration_ms=0,output_chars=len(r['raw'])) for r in row['model_outputs'][2:4]]
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(side_effect=completions))
    result=await im._invoke_model_intent(row['turns'][1]['message'],context_messages=[{'role':'task_state','content':state.model_dump_json()}],timeout_seconds=30)
    assert not result.resolution.clarification_required
    assert len(result.resolution.change_requests)==9

@pytest.mark.parametrize('message,value,field,valid',[
 ('周期六周。现在生成完整提案',6,'schedule.duration_weeks',True),
 ('现在生成完整提案',6,'schedule.duration_weeks',False),
 ('周期不要六周。现在生成完整提案',6,'schedule.duration_weeks',False),
 ('周期八周。现在生成完整提案',6,'schedule.duration_weeks',False),
 ('每周六天。现在生成完整提案',6,'schedule.days_per_week',False),
 ('周期六周，周期仍待补。现在生成完整提案',6,'schedule.duration_weeks',False),
])
def test_conditional_release_requires_actual_gap_fulfillment(message,value,field,valid):
    from app.schemas.agent_task import PlanInputGap
    from app.services.agent_plan_completeness import fulfills_pending_requirement
    gap=PlanInputGap(field_path='schedule.duration_weeks',quote='周期还没决定，等我补充周期后再给完整提案')
    changes=[ChangeRequest(resource='workout_plan',operation='update',field_path=field,value=value)]
    assert fulfills_pending_requirement('等我补充周期后再给完整提案','现在生成完整提案',[gap],changes,message) is valid
