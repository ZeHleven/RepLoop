"""Frozen acceptance: real provider + queue/worker + API + isolated PostgreSQL."""
import asyncio, hashlib, json, os, time
from datetime import date
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.config import settings
from app.models.agent import AgentRun, AgentToolCall, AgentProposal
from app.models.exercise import Exercise
from app.models.food import Food
from app.models.meal import MealItem, MealLog
from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession
from app.services import agent_intent_model as im, agent_runtime
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.auth import create_access_token
from evals.task_fixtures import task_client
from evals.proposal_lifecycle_checks import replaced_draft_references
from evals.tests.test_agent_tasks import seed

HERE=Path(__file__).resolve().parents[1]
CASES=json.loads((HERE/'revision_cases.json').read_text(encoding='utf-8'))['cases']

async def seed_task(db):
    user, profile, conv, foods=await seed(db)
    plan=WorkoutPlan(user_id=user.id,name='第09轮合成验收计划',days_per_week=3,duration_weeks=4)
    db.add(plan); await db.flush()
    for i,(name,weight) in enumerate((('卧推',20),('深蹲',30))):
        ex=Exercise(name_zh=name,name_en='Synthetic '+str(i),category='strength',difficulty='beginner',muscle_primary=['chest' if i==0 else 'quadriceps'])
        db.add(ex); await db.flush()
        db.add(PlannedExercise(plan_id=plan.id,exercise_id=ex.id,day_of_week=1,sets=3,reps='10',rest_seconds=90,recommended_weight_kg=weight,order_index=i))
    for day,status in ((10,'completed'),(12,'completed'),(14,'completed'),(16,'completed'),(15,'in_progress'),(15,'ended_early'),(17,'completed')):
        db.add(WorkoutSession(user_id=user.id,plan_id=plan.id,plan_name=plan.name,trained_at=date(2026,9,day),status=status))
    await db.commit()
    return user.id,conv.id

async def sql_snapshot(db,uid):
    db.expire_all()
    rows=(await db.execute(select(PlannedExercise,Exercise.name_zh).join(WorkoutPlan).join(Exercise,PlannedExercise.exercise_id==Exercise.id).where(WorkoutPlan.user_id==uid,WorkoutPlan.is_active.is_(True)))).all()
    plan={name:{field:getattr(row,field) for field in ('sets','reps','rest_seconds','recommended_weight_kg')} for row,name in rows}
    meals=[]
    for meal in (await db.scalars(select(MealLog).where(MealLog.user_id==uid))).all():
        items=(await db.scalars(select(MealItem).where(MealItem.meal_id==meal.id))).all()
        meals.append({'date':meal.logged_at.isoformat(),'meal_type':meal.meal_type,'items':{item.food_name:item.amount_g for item in items},'item_count':len(items),'calories':sum(item.calories for item in items)})
    return {'plan':plan,'meals':meals}

def outcome_errors(case,snapshot,bodies):
    errors=[]
    if case['kind']=='plan':
        if snapshot['plan']!=case['gold']: errors.append({'check':'final_plan','expected':case['gold'],'actual':snapshot['plan']})
        if snapshot['meals']: errors.append({'check':'unexpected_meal_write'})
    elif case['kind']=='meal':
        actual=snapshot['meals']; gold=case['gold']
        if len(actual)!=1: errors.append({'check':'meal_count','expected':1,'actual':len(actual)})
        else:
            for key in ('date','meal_type','items'):
                if actual[0][key]!=gold[key]: errors.append({'check':key,'expected':gold[key],'actual':actual[0][key]})
            if actual[0]['item_count']!=len(gold['items']): errors.append({'check':'duplicate_items'})
            if abs(actual[0]['calories']-gold['calories'])>.01: errors.append({'check':'calories','expected':gold['calories'],'actual':actual[0]['calories']})
    else:
        for index,(body,gold) in enumerate(zip(bodies,case['gold'])):
            cards=[c for c in body.get('cards',[]) if c['type']=='workout.list_history']
            if len(cards)!=1: errors.append({'check':'history_card','turn':index,'actual_count':len(cards)}); continue
            data=cards[0]['data']; actual=[row['trained_at'] for row in data.get('sessions',[])]
            if actual!=gold['dates'] or data.get('total_count')!=len(gold['dates']): errors.append({'check':'history_scope','turn':index,'expected':gold['dates'],'actual':actual,'count':data.get('total_count')})
            if data.get('status_filter')!=['completed']: errors.append({'check':'completion_filter','turn':index})
            if body.get('proposal'): errors.append({'check':'query_created_proposal','turn':index})
        if snapshot['meals']: errors.append({'check':'query_wrote_meal'})
    return errors

@pytest.mark.asyncio
async def test_fixture(db_session):
    uid,_=await seed_task(db_session)
    snap=await sql_snapshot(db_session,uid)
    assert len(snap['plan'])==2 and not snap['meals']
    assert outcome_errors(CASES[0],snap,[]), 'Negative control must reject unchanged plan'
    assert outcome_errors(CASES[3],snap,[]), 'Negative control must reject missing meal'
    assert outcome_errors(CASES[6],snap,[{},{}]), 'Negative control must reject missing history'

@pytest.mark.asyncio
@pytest.mark.parametrize('case',CASES,ids=[c['id'] for c in CASES])
async def test_new_task(case,task_client,db_session,session_factory,monkeypatch):
    live=os.environ.get('REVISION_EVAL_MODE')=='live'
    uid,cid=await seed_task(db_session)
    headers={'Authorization':'Bearer '+create_access_token(uid)}
    # Match default planned execution, explicitly enable local proposal capabilities.
    monkeypatch.setattr(settings,'AGENT_PLANNED_EXECUTION_ENABLED',True)
    monkeypatch.setattr(settings,'AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED',True)
    rec={'id':case['id'],'title':case['title'],'data_source':'synthetic','mode':'live' if live else 'scripted','http':[],'turns':[],'model_outputs':[],'sql':[],'checks':[],'flags':{'planned_execution':True,'nutrition_proposals':True,'plan_management_proposals':True},'gold_sha256':hashlib.sha256((HERE/'revision_cases.json').read_bytes()).hexdigest()}
    output=Path(os.environ['REVISION_EVAL_DIR'])/(case['id']+'.json')
    def save(): output.write_text(json.dumps(rec,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    original=im.structured_chat_completion
    current_turn=0
    fixtures=json.loads((HERE/'revision_model_fixtures.json').read_text(encoding='utf-8'))[case['id']]
    if not live:
        monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','scripted-no-network')
        def no_network(*args,**kwargs):
            raise AssertionError('Unexpected free-form model call in scripted regression')
        monkeypatch.setattr(agent_runtime,'_build_model',no_network)
    async def capture(messages,**kw):
        started=time.perf_counter()
        try:
            if live:
                result=await original(messages,**kw)
            else:
                from app.services.ai_client import StructuredCompletionResult
                stage='route' if kw['function_name']=='submit_semantic_route' else 'extraction'
                payload=json.loads(json.dumps(fixtures[current_turn][stage]))
                if stage=='route':
                    # Dynamic server IDs, and valid route edits. The bad remove=true
                    # from N02 is separately covered by the source/withdrawal tests.
                    action='new' if current_turn==0 or case['id']=='N03' and current_turn==1 else 'resume' if case['id']=='N03' else 'continue'
                    task_id=rec['turns'][0]['body']['id'] if action in {'continue','resume'} else ''
                    payload['task_update']={'action':action,'task_id':task_id,'trigger':case['turns'][current_turn],'requirements':[]}
                else:
                    payload['pending_plan_withdrawals']=[]
                raw=json.dumps(payload,ensure_ascii=False)
                result=StructuredCompletionResult(payload=payload,raw_output=raw,mode='scripted_replay',finish_reason='stop',duration_ms=0,output_chars=len(raw))
            rec['model_outputs'].append({'stage':kw.get('function_name'),'messages':messages,'raw':result.raw_output,'payload':result.payload,'mode':result.mode,'duration_ms':round((time.perf_counter()-started)*1000)})
            save(); return result
        except Exception as exc:
            rec['model_outputs'].append({'stage':kw.get('function_name'),'messages':messages,'error_type':type(exc).__name__,'duration_ms':round((time.perf_counter()-started)*1000)})
            save(); raise
    monkeypatch.setattr(im,'structured_chat_completion',capture)
    direct=agent_runtime.invoke_langchain_agent
    async def capture_direct(*a,**kw):
        result=await direct(*a,**kw)
        rec.setdefault('direct_messages',[]).append([m.model_dump(mode='json') for m in result.get('messages',[])])
        save(); return result
    monkeypatch.setattr(agent_runtime,'invoke_langchain_agent',capture_direct)
    async def request(method,path,**kw):
        started=time.perf_counter()
        r=await task_client.request(method,path,headers=headers,**kw)
        rec['http'].append({'method':method,'path':path,'request':kw.get('json'),'status':r.status_code,'body':r.json(),'elapsed_ms':round((time.perf_counter()-started)*1000)})
        save(); assert r.status_code in (200,202),r.text
        return r.json()
    started=time.perf_counter(); bodies=[]
    try:
        baseline=await sql_snapshot(db_session,uid); rec['sql'].append({'stage':'baseline',**baseline})
        for index,message in enumerate(case['turns']):
            current_turn=index
            turn_start=time.perf_counter()
            submitted=await request('POST','/api/v1/agent/runs',json={'conversation_id':cid,'message':message,'client_request_id':'round09-'+uuid4().hex})
            rid=submitted['run_id']
            async with session_factory() as worker_db:
                claimed=await claim_next_agent_run(worker_db)
            assert claimed==rid,'Fixture claim mismatch'
            await asyncio.wait_for(process_agent_run(session_factory,rid),timeout=180)
            body=await request('GET','/api/v1/agent/runs/'+rid)
            bodies.append(body)
            rec['turns'].append({'index':index,'message':message,'body':body,'wait_ms':round((time.perf_counter()-turn_start)*1000)})
            snapshot=await sql_snapshot(db_session,uid); rec['sql'].append({'stage':'after_turn_'+str(index),**snapshot})
            if snapshot!=baseline: rec['checks'].append({'check':'write_before_confirmation','turn':index,'actual':snapshot})
            save()
        if case['kind']=='meal':
            # A revised draft must retire the earlier card, not only carry values.
            for old_ref in replaced_draft_references(bodies):
                old=await db_session.get(AgentProposal,old_ref['id'])
                await db_session.refresh(old)
                if old.status!='stale':
                    rec['checks'].append({'check':'superseded_meal_still_pending','actual':old.status})
                old_path=f"/api/v1/proposals/{old_ref['id']}/confirm"
                old_request={'expected_version':old_ref['version'],'client_request_id':'round12-old-'+uuid4().hex}
                rejected=await task_client.post(old_path,headers=headers,json=old_request)
                rec['http'].append({'method':'POST','path':old_path,'request':old_request,'status':rejected.status_code,'body':rejected.json()})
                if rejected.status_code!=409:
                    rec['checks'].append({'check':'superseded_meal_confirmed','status':rejected.status_code})
                if await sql_snapshot(db_session,uid)!=baseline:
                    rec['checks'].append({'check':'superseded_meal_wrote_record'})
                save()
        if case['kind']!='query':
            ref=bodies[-1].get('proposal')
            if ref:
                payload={'expected_version':ref['version'],'client_request_id':'round09-confirm-'+uuid4().hex}
                result=await request('POST',f"/api/v1/proposals/{ref['id']}/confirm",json=payload)
                again=await request('POST',f"/api/v1/proposals/{ref['id']}/confirm",json=payload)
                if result!=again: rec['checks'].append({'check':'confirm_not_idempotent'})
            else: rec['checks'].append({'check':'missing_final_proposal'})
        final=await sql_snapshot(db_session,uid); rec['sql'].append({'stage':'final',**final})
        rec['checks'].extend(outcome_errors(case,final,bodies))
        if case['kind']=='meal':
            if final['plan']!=baseline['plan']: rec['checks'].append({'check':'meal_altered_plan'})
            await request('GET','/api/v1/meals/history')
        elif case['kind']=='query' and final!=baseline: rec['checks'].append({'check':'query_business_write'})
        reopened=await request('GET',f'/api/v1/agent/conversations/{cid}/messages')
        for turn in rec['turns']:
            if not any(m.get('run_id')==turn['body']['id'] and m.get('role')=='assistant' and m.get('content')==turn['body'].get('reply') for m in reopened):
                rec['checks'].append({'check':'reply_not_persisted','turn':turn['index']})
        rec['passed']=not rec['checks']
    except Exception as exc:
        rec['passed']=False; rec['error']={'type':type(exc).__name__,'message':str(exc)[:2500]}
        raise
    finally:
        await db_session.rollback(); db_session.expire_all()
        runs=list((await db_session.scalars(select(AgentRun).where(AgentRun.user_id==uid).order_by(AgentRun.queued_at))).all())
        rec['runs']=[{'id':r.id,'status':r.status,'request_kind':r.request_kind,'resolved_query':r.resolved_query,'trace':r.execution_trace,'error_code':r.error_code,'duration_ms':r.duration_ms,'input_tokens':r.input_tokens,'output_tokens':r.output_tokens} for r in runs]
        by_id={r.id:r for r in runs}
        rec['queued_at_in_submission_order']=[{'id':t['body']['id'],'queued_at':by_id[t['body']['id']].queued_at.isoformat()} for t in rec['turns']]
        queue_times=[by_id[t['body']['id']].queued_at for t in rec['turns']]
        rec['queue_time_reversed']=any(b<a for a,b in zip(queue_times,queue_times[1:]))
        audits=list((await db_session.scalars(select(AgentToolCall).where(AgentToolCall.run_id.in_([r.id for r in runs])))).all())
        rec['tool_audits']=[{'run_id':a.run_id,'tool':a.tool_name,'arguments':a.arguments_data,'result':a.result_data,'status':a.status} for a in audits]
        proposals=list((await db_session.scalars(select(AgentProposal).where(AgentProposal.user_id==uid))).all())
        rec['proposals']=[{'id':p.id,'status':p.status,'payload':p.payload_data} for p in proposals]
        rec['total_elapsed_ms']=round((time.perf_counter()-started)*1000)
        rec['clarification_runs']=sum(bool(t['body'].get('clarification_required')) for t in rec['turns'])
        rec['planned_user_revisions']=len(case['turns'])-1
        rec['recovery_corrections']='not_measured: fixed script stops after planned turns, no adaptive rescue'
        save()
    assert rec['passed'],json.dumps(rec['checks'],ensure_ascii=False)
