"""Scripted model HTTP, real API/worker/SQL and proposal decisions."""
import copy,json,os,math
from pathlib import Path
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.config import settings
from app.main import app
from app.models.agent import AgentArtifact,AgentProposal,AgentRun,AgentToolCall,AgentConversation
from app.models.food import Food
from app.models.meal import MealLog,MealItem
from app.services.auth import create_access_token
from test_agent_daily_meal_plans import _daily_context,_artifact
from test_round76_source_scope_sql import install,run,function_message
from test_round57_read_delivery import scripted_http
from round80_support import DATA,MESSAGE,payload,bad_route,partial

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

async def seed(db,placement):
    user,conv,source_run,foods=await _daily_context(db,'r80-'+uuid4().hex)
    other,other_conv,other_run,other_foods=await _daily_context(db,'r80-other-'+uuid4().hex)
    for food in DATA['case']['foods']:
        if food['name'] not in {'土豆','鱼肉'}:continue
        db.add(Food(id='r80-food-'+uuid4().hex,name_zh=food['name'],category=food['category'],calories_per_100g=food['calories'],protein_g=food['protein_g'],carbs_g=food['carbs_g'],fat_g=food['fat_g'],is_active=True))
    await db.commit()
    artifact=None
    if placement=='owned':artifact=await _artifact(db,user=user,conversation=conv,run=source_run,foods=foods)
    elif placement=='foreign':artifact=await _artifact(db,user=other,conversation=other_conv,run=other_run,foods=other_foods)
    elif placement=='other_conversation':
        extra=AgentConversation(user_id=user.id);db.add(extra);await db.commit()
        artifact=await _artifact(db,user=user,conversation=extra,run=source_run,foods=foods)
    return user,conv,other,artifact

def artifact_snapshot(artifact):
    return None if artifact is None else dict(id=artifact.id,owner=artifact.user_id,conversation=artifact.conversation_id,status=artifact.status,version=artifact.version,payload=copy.deepcopy(artifact.payload_data),fingerprint=artifact.payload_fingerprint)

def save_evidence(name,evidence):
    dest=os.environ.get('R80_EVIDENCE_DIR')
    if dest:(Path(dest)/('api-'+name+'-'+uuid4().hex+'.json')).write_text(json.dumps({'model':'scripted HTTP; not live model quality','source':'R74 original synthetic HM02 plus registered boundary variants',**evidence},ensure_ascii=False,indent=2),encoding='utf8')

@pytest.mark.asyncio
@pytest.mark.parametrize('repeat,placement',[(1,'none'),(2,'none'),(2,'owned'),(2,'foreign'),(2,'other_conversation')])
async def test_original_three_turns_replace_proposal_then_confirm_exactly_once(db_session,session_factory,monkeypatch,repeat,placement):
    user,conv,other,artifact=await seed(db_session,placement);uid=user.id;aid=artifact.id if artifact else None
    before=artifact_snapshot(artifact);install(monkeypatch,session_factory);monkeypatch.setattr(settings,'AGENT_NUTRITION_PROPOSALS_ENABLED',True)
    sequence=[];requests=scripted_http(monkeypatch,sequence);evidence={'repeat':repeat,'placement':placement,'turns':[],'artifact_before':before};last=None;proposals=[]
    catalog=(await db_session.scalars(select(Food).where(Food.name_zh.in_(['土豆','鱼肉'])))).all()
    local_ids={f.name_zh:f.id for f in catalog};assert len(catalog)==len(local_ids)==2
    evidence['fixture_id_mapping']={'r31-potato':local_ids['土豆'],'r31-fish':local_ids['鱼肉']}
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(uid)}) as client:
            for turn,message in enumerate(DATA['case']['turns']):
                route=payload(repeat,turn,'submit_semantic_route')
                if last:route['task_update']['task_id']=last['execution_trace']['task_state']['active_task_id']
                # R2 never called the extractor on turn zero. Supply the
                # sibling R1 archived partial extraction after the repaired
                # branch; do not call this an unchanged full-response replay.
                extraction=partial() if turn==0 else payload(repeat,turn,'submit_domain_changes')
                # Archived catalog identities belong to the original database.
                # Remap only known IDs; keep all business values and archive intact.
                for change in extraction['change_requests']:
                    value=json.loads(change['value_json'])
                    for item in value.get('items',[]):
                        if item.get('food_id'):
                            mapped=evidence['fixture_id_mapping'][item['food_id']]
                            assert mapped==local_ids[item['food_name']]
                            item['food_id']=mapped
                    change['value_json']=json.dumps(value,ensure_ascii=False)
                sequence.extend([function_message('submit_semantic_route',route,f'route-{turn}'),function_message('submit_domain_changes',extraction,f'change-{turn}')])
                body=await run(client,session_factory,conv,message);evidence['turns'].append(body)
                assert body['status']=='completed' and body['error_code'] is None,body
                assert len(requests)==2*(turn+1)
                if turn==0:
                    assert body['clarification_required'] and '鱼肉' in body['reply'] and '克数' in body['reply'],body
                    assert '全天' not in body['reply'] and not body.get('proposal')
                    items=body['change_requests'][0]['value']['items'];assert items[0]['amount_g']==180 and items[1]['amount_g'] is None
                    assert body['execution_trace']['task_state']['tasks'][0]['phase']=='awaiting_input'
                else:
                    assert not body['clarification_required'] and body['proposal']['proposal_type']=='meal_log_create_v1',body
                    proposals.append(body['proposal'])
                    async with session_factory() as db:
                        proposal=await db.get(AgentProposal,body['proposal']['id']);after=proposal.payload_data['after'];gold=DATA['case']['gold'][turn]
                        assert after['logged_at']==gold['date'] and after['meal_type']==gold['meal_type']
                        assert {i['food_name']:i['amount_g'] for i in after['items']}==gold['items']
                        assert math.isclose(sum(i['calories'] for i in after['items']),gold['calories'])
                        evidence['turns'][-1]['verified_proposal_payload']=copy.deepcopy(proposal.payload_data)
                async with session_factory() as db:
                    assert await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==uid))==0
                    if aid:assert artifact_snapshot(await db.get(AgentArtifact,aid))==before
                    stored=await db.get(AgentRun,body['id']);assert stored.execution_trace==body['execution_trace']
                    calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==body['id']))).all()
                    evidence['turns'][-1]['tool_audits']=[dict(tool=c.tool_name,status=c.status,arguments=c.arguments_data,result=c.result_data) for c in calls]
                last=body
            first,final=proposals
            stale=await client.post('/api/v1/proposals/'+first['id']+'/confirm',json={'expected_version':first['version'],'client_request_id':uuid4().hex})
            assert stale.status_code==409,stale.text
            wrong_version=await client.post('/api/v1/proposals/'+final['id']+'/confirm',json={'expected_version':999,'client_request_id':uuid4().hex})
            assert wrong_version.status_code==409,wrong_version.text
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(other.id)}) as foreign:
                denied=await foreign.post('/api/v1/proposals/'+final['id']+'/confirm',json={'expected_version':final['version'],'client_request_id':uuid4().hex})
                assert denied.status_code==404,denied.text
            decision={'expected_version':final['version'],'client_request_id':uuid4().hex}
            confirm=await client.post('/api/v1/proposals/'+final['id']+'/confirm',json=decision)
            replay=await client.post('/api/v1/proposals/'+final['id']+'/confirm',json=decision)
            assert confirm.status_code==replay.status_code==200,(confirm.text,replay.text)
            assert confirm.json()==replay.json() and confirm.json()['status']=='applied'
            assert len(requests)==6,'Decisions and reads must not invoke a model'
            evidence['decisions']={'stale':stale.json(),'wrong_version':wrong_version.json(),'foreign':denied.json(),'confirm':confirm.json(),'replay':replay.json()}
        async with session_factory() as db:
            meals=(await db.scalars(select(MealLog).where(MealLog.user_id==uid))).all();assert len(meals)==1
            assert str(meals[0].logged_at)=='2026-10-06' and meals[0].meal_type=='晚餐'
            items=(await db.scalars(select(MealItem).where(MealItem.meal_id==meals[0].id))).all()
            assert {i.food_name:i.amount_g for i in items}=={'土豆':230,'鱼肉':130}
            assert math.isclose(sum(i.calories for i in items),333.1)
            assert (await db.get(AgentProposal,first['id'])).status=='stale' and (await db.get(AgentProposal,final['id'])).status=='applied'
            if aid:assert artifact_snapshot(await db.get(AgentArtifact,aid))==before
            assert await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==other.id))==0
            evidence.update(passed=True,meal_count=1,final_items={i.food_name:i.amount_g for i in items},final_calories=sum(i.calories for i in items),artifact_unchanged=True)
    finally:
        app.dependency_overrides.clear();evidence['requests']=requests;save_evidence(f'hm02-r{repeat}-{placement}',evidence)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['natural','card'])
async def test_actual_whole_artifact_save_still_needs_confirmation(db_session,session_factory,monkeypatch,mode):
    user,conv,_,artifact=await seed(db_session,'owned');uid=user.id;aid=artifact.id
    install(monkeypatch,session_factory);monkeypatch.setattr(settings,'AGENT_NUTRITION_PROPOSALS_ENABLED',True)
    message='保存这份方案';route=bad_route(message)
    requests=scripted_http(monkeypatch,[function_message('submit_semantic_route',route,'save')])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(uid)}) as client:
            if mode=='natural':body=await run(client,session_factory,conv,message)
            else:
                response=await client.post('/api/v1/agent/chat',json={'conversation_id':conv.id,'message':message,'artifact_action':{'action':'save_as_proposal','artifact_id':aid,'expected_version':artifact.version,'payload_fingerprint':artifact.payload_fingerprint}})
                assert response.status_code==200,response.text
                body=response.json()
            assert body['proposal']['proposal_type']=='daily_meal_log_create_v1',body
        async with session_factory() as db:
            stored=await db.get(AgentArtifact,aid);assert stored.status=='proposed'
            p=await db.get(AgentProposal,body['proposal']['id']);assert p.target_id==aid and p.status=='pending_confirmation'
            assert await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==uid))==0
        assert len(requests)==(1 if mode=='natural' else 0)
        save_evidence('artifact-'+mode,dict(passed=True,body=body,requests=requests,meal_count=0,artifact_id=aid))
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
    '记录晚餐鱼肉130克；保存刚才的全天方案。',
    '我不想保存这份方案。',
])
async def test_invalid_artifact_targets_never_create_or_confirm(db_session,session_factory,monkeypatch,message):
    user,conv,_,artifact=await seed(db_session,'owned');uid=user.id;before=artifact_snapshot(artifact)
    install(monkeypatch,session_factory);monkeypatch.setattr(settings,'AGENT_NUTRITION_PROPOSALS_ENABLED',True)
    route=bad_route(message);retry=copy.deepcopy(route)
    if message.startswith('记录'):retry['artifact_action']='none'
    requests=scripted_http(monkeypatch,[function_message('submit_semantic_route',r,'route-'+str(i)) for i,r in enumerate((route,retry))])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(uid)}) as client:
            body=await run(client,session_factory,conv,message)
            assert not body.get('proposal'),body
        async with session_factory() as db:
            assert artifact_snapshot(await db.get(AgentArtifact,artifact.id))==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==uid))==0
            assert await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==uid))==0
        assert len(requests)<=2
        save_evidence('invalid-target',dict(passed=True,message=message,body=body,requests=requests,artifact_unchanged=True,meal_count=0,proposal_count=0))
    finally:app.dependency_overrides.clear()
