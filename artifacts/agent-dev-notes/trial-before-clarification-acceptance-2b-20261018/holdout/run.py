"""Real API/worker/provider runner over fresh local-only PostgreSQL schemas."""
import asyncio
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from capture import ProviderCapture
from evals.attempt_capture import collect_attempt_snapshot, record_attempt_snapshot
from evals.attempt_metrics import summarize_batch
from eval_support import Budget,EvaluationStopped,atomic_json,recorded_request,guarded_wait,elapsed_validity
from continuation_budget import ContinuationBudget as Budget
from app.services.business_clock import BUSINESS_DATE,business_today
from metrics import ToolRecorder, CONTEXT, upsert_attempt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema
from app import models
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.agent import AgentConversation, AgentRun, AgentProposal, AgentArtifact, AgentToolCall
from app.models.user import User
from app.models.profile import UserProfile, WeightLog
from app.models.food import Food
from app.models.meal import MealLog, MealItem
from app.models.exercise import Exercise
from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
from app.services import agent_runtime
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.auth import create_access_token
from evals.postgres_test_fixtures import query_eval_database_url
from score import BENCH, ROOT, NUTRIENTS, FOODS, proposal_errors, score, selftest

SCHEMA_PREFIX = 'trialaccept2bholdout_'
REGISTRY = None

def utc(): return datetime.now(timezone.utc).isoformat()
def dump(path, value): atomic_json(path,value)
def serial(row):
    return json.loads(json.dumps({c.name: getattr(row, c.name) for c in row.__table__.columns}, default=str))

async def setup(case):
    url = query_eval_database_url()
    schema = SCHEMA_PREFIX + uuid4().hex
    if CURRENT is not None:CURRENT['schema']=schema;save_current()
    control = create_async_engine(url, poolclass=NullPool)
    async with control.begin() as conn: await conn.execute(CreateSchema(schema))
    await control.dispose()
    engine = create_async_engine(url, poolclass=NullPool, connect_args={'server_settings': {'search_path': schema+',public', 'timezone':'UTC'}})
    async with engine.begin() as conn: await conn.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        uid = 'synthetic-r46-' + uuid4().hex
        db.add(User(id=uid, email=uid+'@example.invalid', password_hash='synthetic-no-login'))
        await db.flush()
        profile = {**BENCH['profile'], **case.get('profile', {}), 'diet_restriction':case.get('diet_restriction','无')}
        db.add(UserProfile(user_id=uid, **profile))
        db.add(WeightLog(user_id=uid, weight_kg=profile['weight_kg'], recorded_at=datetime(2026,10,8,0,0,tzinfo=timezone.utc)))
        conv = AgentConversation(user_id=uid); db.add(conv)
        for f in case.get('foods', BENCH['foods']):
            db.add(Food(id='r31-'+f['id'], name_zh=f['name'], category=f['category'], calories_per_100g=f['calories'], protein_g=f['protein_g'], carbs_g=f['carbs_g'], fat_g=f['fat_g'], diet_tags=f['diet_tags'], is_common_in_china=True, is_active=True))
        bp = case.get('plan', BENCH['plan']); plan = WorkoutPlan(user_id=uid, **{k:v for k,v in bp.items() if k!='exercises'})
        db.add(plan); await db.flush()
        for i, row in enumerate(bp['exercises']):
            ex = Exercise(id='r31-ex-'+str(i), name_zh=row['exercise_name'], name_en='Synthetic '+str(i), category='strength', difficulty='beginner', muscle_primary=['chest' if i==0 else 'quadriceps'])
            db.add(ex); await db.flush()
            db.add(PlannedExercise(plan_id=plan.id, exercise_id=ex.id, **{k:v for k,v in row.items() if k!='exercise_name'}))
        for day, status in case.get('sessions', []):
            started = datetime.fromisoformat(day+'T08:00:00+00:00')
            ended = datetime.fromisoformat(day+'T09:00:00+00:00') if status != 'in_progress' else None
            linked = {}
            if day in case.get('linked_session_dates', []):
                from app.services.training_lifecycle import training_week
                d = date.fromisoformat(day)
                linked = dict(plan_family_id=plan.family_id, week_start=training_week(d), day_of_week=d.isoweekday())
            db.add(WorkoutSession(user_id=uid, plan_id=plan.id, plan_name=plan.name, trained_at=date.fromisoformat(day), status=status, started_at=started, completed_at=ended, **linked))
        for meal in case.get('meals', []):
            row = MealLog(user_id=uid, logged_at=date.fromisoformat(meal['date']), meal_type=meal['meal_type']); db.add(row); await db.flush()
            for name, grams in meal['items'].items():
                f = FOODS[name]
                db.add(MealItem(meal_id=row.id, food_id='r31-'+f['id'], food_name=name, amount_g=grams, **{k:round(f[k]*grams/100,1) for k in NUTRIENTS}))
        # Seed business timestamps on the synthetic business calendar. Host SQL
        # defaults must not make a pre-existing plan appear created in the future.
        await db.flush()
        fixture_day = datetime.fromisoformat(BENCH['business_date']+'T00:00:00+00:00')
        for model in (UserProfile, WeightLog, WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise, MealLog, MealItem):
            for obj in (await db.scalars(select(model))).all():
                event_day = getattr(obj, 'trained_at', None) or getattr(obj, 'logged_at', None)
                stamp = min(datetime.combine(event_day, datetime.min.time(), tzinfo=timezone.utc), fixture_day) if event_day else datetime(2026,9,1,tzinfo=timezone.utc)
                if hasattr(obj, 'created_at'):obj.created_at = stamp
                if hasattr(obj, 'updated_at'):obj.updated_at = stamp
        await db.commit(); cid = conv.id
    return engine, factory, uid, cid, schema

async def snapshot(factory):
    # Only this case's isolated schema is visible. Include all business rows,
    # excluding auth users and expected Agent conversation/artifact persistence.
    result = {}
    async with factory() as db:
        for model in (UserProfile, WeightLog, WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise, MealLog, MealItem):
            rows = [serial(r) for r in (await db.scalars(select(model))).all()]
            result[model.__tablename__] = sorted(rows, key=lambda x:x['id'])
    return result

async def view(factory):
    async with factory() as db:
        plans = list((await db.scalars(select(WorkoutPlan).where(WorkoutPlan.is_active.is_(True)))).all())
        plan = {}
        if len(plans) == 1:
            p = plans[0]; plan = {k:getattr(p,k) for k in ('name','goal','duration_weeks','days_per_week')}
            rows = (await db.execute(select(PlannedExercise, Exercise.name_zh).join(Exercise, PlannedExercise.exercise_id==Exercise.id).where(PlannedExercise.plan_id==p.id))).all()
            plan['exercises'] = [{**serial(r), 'exercise_name':name} for r,name in rows]
        meals = []
        for m in (await db.scalars(select(MealLog))).all():
            meals.append({**serial(m), 'items':[serial(x) for x in (await db.scalars(select(MealItem).where(MealItem.meal_id==m.id))).all()]})
        return {'plan':plan, 'meals':meals}

async def export(factory, rec):
    async with factory() as db:
        for key, model in (('runs',AgentRun), ('proposals',AgentProposal), ('artifacts',AgentArtifact), ('tool_audits',AgentToolCall)):
            rec[key] = [serial(r) for r in (await db.scalars(select(model))).all()]

BUDGET = None
CURRENT = None
RUN_COUNT = 0
SUITE_START = time.monotonic()
def capture_sink(row):
    if CURRENT is None or os.environ.get('R31_MODE') != 'live': raise RuntimeError('Model call forbidden during preflight')
    existing = next((i for i,p in enumerate(CURRENT['provider']) if p['index']==row['index']), None)
    if existing is None: CURRENT['provider'].append(dict(row))
    else: CURRENT['provider'][existing]=dict(row)
    save_current()
def guard_sink(row):
    if CURRENT is None:raise RuntimeError('Guard event outside registered instance')
    CURRENT['provider_guard_events'].append(row);save_current()
CAPTURE = ProviderCapture(capture_sink, max_requests=BENCH['limits']['max_provider_requests'], max_seconds=BENCH['limits']['suite_seconds'],guard_sink=guard_sink)

TOOLS = ToolRecorder(lambda row: (upsert_attempt(CURRENT["tool_attempts"],row), save_current()))

def save_current():
    if CURRENT: dump(ROOT/'runs'/(CURRENT['instance']+'.json'), CURRENT)

async def run_case(case, repeat):
    global CURRENT, RUN_COUNT
    rec = {'instance':case['id']+'-r'+str(repeat),'case_id':case['id'],'group':case['group'],'repeat':repeat,'started_at':utc(),'data_source':'synthetic','tool_attempts':[],'turns':[],'http':[],'provider':[],'confirmation_errors':[], 'freeze_sha256':hashlib.sha256((ROOT/'freeze.json').read_bytes()).hexdigest()}
    BUDGET.begin(rec['instance'])
    rec['provider_guard_events']=[]
    rec['attempt_observation_version']=1
    CURRENT = rec; save_current(); engine = None; factory = None
    try:
        engine, factory, uid, cid, schema = await setup(case)
        REGISTRY.register(BUDGET,rec['instance'],schema=schema,user_id=uid,conversation_id=cid)
        rec.update(user_id=uid, conversation_id=cid, schema=schema, baseline=await snapshot(factory))
        async def override():
            async with factory() as db: yield db
        app.dependency_overrides[get_db] = override
        agent_runtime.AsyncSessionLocal = factory
        headers = {'Authorization':'Bearer '+create_access_token(uid)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic-test') as client:
            async def request(method, path, payload=None, *, expected_outcome=None):
                return await recorded_request(client,method,path,payload,headers,lambda:create_access_token(uid),rec['http'],save_current,budget=BUDGET,expected_outcome=expected_outcome)
            for i, message in enumerate(case['turns']):
                rec['current_turn'] = i
                if RUN_COUNT >= BENCH['limits']['max_runs']: raise RuntimeError('Run budget reached')
                CAPTURE.context = {'instance':rec['instance'],'turn':i}
                CONTEXT.set(dict(CAPTURE.context))
                RUN_COUNT = BUDGET.reserve('runs')
                assert business_today().isoformat()==BENCH['business_date']
                start_wall=time.time();start = time.monotonic()
                r = await request('POST','/api/v1/agent/runs',{'conversation_id':cid,'message':message,'client_request_id':'round51-'+rec['instance']+'-'+str(i)})
                if r.status_code not in (200,202): raise RuntimeError('Submission HTTP '+str(r.status_code))
                rid = r.json()['run_id']
                async with factory() as db: claimed = await claim_next_agent_run(db)
                if claimed != rid: raise RuntimeError('Fixture claim mismatch')
                await guarded_wait(process_agent_run(factory,rid), BUDGET, BENCH['limits']['run_seconds'])
                r = await request('GET','/api/v1/agent/runs/'+rid)
                body = r.json();timing=elapsed_validity(start_wall,start)
                # Timing-quality exclusions do not authorize or extend work.
                # The cumulative execution budget is checked independently.
                terminal=body.get('status') in ('completed','failed','cancelled')
                clock_valid=timing['clock_valid'] and rec['http'][-1]['clock_valid'] and not any(word in str(BUDGET.state['stop']) for word in ('clock','monotonic'))
                turn = {'index':i,'message':message,'body':body,'api_to_terminal_ms':timing['elapsed_ms'],'api_to_terminal_wall_ms':timing['elapsed_wall_ms'],'clock_deviation_ms':timing['clock_deviation_ms'],'latency_valid':terminal and clock_valid,'latency_scope':'submission_to_terminal','latency_invalid_reason':None if terminal and clock_valid else 'clock_discontinuity' if not clock_valid else 'nonterminal_response','business':await snapshot(factory)}
                async with factory() as db:
                    ref = body.get('proposal')
                    turn['proposal'] = serial(await db.get(AgentProposal,ref['id'])) if ref else None
                    artifact = await db.scalar(select(AgentArtifact).where(AgentArtifact.source_run_id==rid).order_by(AgentArtifact.created_at.desc()).limit(1))
                    turn['artifact'] = serial(artifact) if artifact else None
                rec['turns'].append(turn); await export(factory,rec); save_current()
                observation=await collect_attempt_snapshot(request,factory,run_id=rid,user_id=uid,conversation_id=cid)
                record_attempt_snapshot(rec,observation);save_current()
                BUDGET.check()
                if not terminal:raise RuntimeError('Worker returned without terminal Run; response retained')
                if CAPTURE.fatal:
                    rec['classification']='provider_environment_error'
                    raise RuntimeError('Stop batch after '+CAPTURE.fatal)
            if case['kind'] in ('meal_write','plan_write'):
                latest = rec['turns'][-1].get('proposal')
                rec['confirm_gate_errors'] = proposal_errors(case,len(case['turns'])-1,latest)
                if not rec['confirm_gate_errors']:
                    if case['kind']=='meal_write':
                        for t in rec['turns'][:-1]:
                            old = t.get('proposal')
                            if old and old['id'] != latest['id']:
                                async with factory() as db: current = await db.get(AgentProposal,old['id'])
                                if current.status!='stale': rec['confirmation_errors'].append('superseded_not_stale')
                                expected = {'status':409,'code':'proposal_not_pending','reason':'superseded_proposal'} if current.status=='stale' else None
                                r = await request('POST','/api/v1/proposals/'+old['id']+'/confirm',{'expected_version':old['version'],'client_request_id':'old-'+uuid4().hex},expected_outcome=expected)
                                if r.status_code!=409: rec['confirmation_errors'].append('superseded_confirm_not_409')
                                if await snapshot(factory)!=rec['baseline']: rec['confirmation_errors'].append('superseded_wrote_business')
                    payload = {'expected_version':latest['version'],'client_request_id':'confirm-'+uuid4().hex}
                    path = '/api/v1/proposals/'+latest['id']+'/confirm'
                    first = await request('POST',path,payload)
                    middle = await snapshot(factory)
                    again = await request('POST',path,payload)
                    rec['confirmed'] = first.status_code==200 and first.json().get('status')=='applied'
                    rec['idempotent'] = first.status_code==again.status_code==200 and first.json()==again.json() and middle==await snapshot(factory)
            r = await request('GET','/api/v1/agent/conversations/'+cid+'/messages')
            rec['reopened'] = r.json() if r.status_code==200 else []
            rec['final'] = await snapshot(factory); rec['final_view'] = await view(factory)
    except BaseException as exc:
        rec['error'] = {'type':type(exc).__name__, 'message':str(exc)[:1500], 'traceback':traceback.format_exc()[-6000:]}
        if not rec.get('baseline'): rec['classification'] = 'fixture_error'
    finally:
        if factory:
            try:
                await export(factory,rec)
                if 'final' not in rec: rec['final']=await snapshot(factory); rec['final_view']=await view(factory)
            except Exception as exc: rec['export_error']=type(exc).__name__
        app.dependency_overrides.pop(get_db,None)
        if engine: await engine.dispose()
        rec['finished_at'] = utc(); rec['score']=score(case,rec); save_current()
        unresolved = any(r.get('status') in ('running','queued') for r in rec.get('runs',[])) or any(not p.get('lifecycle_complete') for p in rec.get('provider',[]))
        if not unresolved:BUDGET.finish(rec['instance'])
        CURRENT=None
    print(json.dumps({'instance':rec['instance'],'structural_pass':rec['score']['structural_pass'],'provider_requests':len(rec['provider']),'error':rec.get('error',{}).get('type')},ensure_ascii=False),flush=True)
    collected=[json.loads(p.read_text(encoding='utf8')) for p in sorted((ROOT/'runs').glob('*.json'))]
    summary=summarize_batch(collected)
    dump(ROOT/('execution-summary-'+uuid4().hex+'.json'),summary)
    return rec

async def main():
    global SUITE_START, BUDGET, RUN_COUNT, REGISTRY
    mode = os.environ.get('R31_MODE','preflight')
    assert BENCH['limits']['planned_runs'] == sum(len(c['turns']) for c in BENCH['cases']) * BENCH['repeats']
    assert len({c['id'] for c in BENCH['cases']}) == len(BENCH['cases'])
    CAPTURE.install()
    if mode != 'preflight':
        TOOLS.install()
    settings.AGENT_PLANNED_EXECUTION_ENABLED = True
    settings.AGENT_NUTRITION_PROPOSALS_ENABLED = True
    settings.AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED = True
    BUSINESS_DATE.set(date.fromisoformat(BENCH['business_date']))
    assert business_today().isoformat()==BENCH['business_date']
    if mode=='preflight':
        report = {'started_at':utc(),'scorer':selftest(),'fixtures':[], 'model_calls':0, 'python':sys.version}
        for case in BENCH['cases']:
            counter_preflight = None
            engine,factory,uid,cid,schema = await setup(case)
            baseline = await snapshot(factory); v = await view(factory)
            assert len(baseline['user_profiles'])==1 and len(baseline['planned_exercises'])==len(case.get('plan',BENCH['plan'])['exercises'])
            for rows in baseline.values():
                for row in rows:
                    for field in ('created_at', 'updated_at'):
                        if row.get(field):assert row[field][:10] <= BENCH['business_date']
            assert len(v['meals'])==len(case.get('meals',[]))
            import math
            totals={}
            for meal in v['meals']:
                if meal['logged_at']>BENCH['business_date']:continue
                row=totals.setdefault(meal['logged_at'],dict.fromkeys(NUTRIENTS,0.))
                for item in meal['items']:
                    for k in NUTRIENTS:row[k]+=item[k]
            assert set(totals)=={g['date'] for g in case['expected_rows']}
            for gold in case['expected_rows']:
                assert all(math.isclose(totals[gold['date']][k],gold[k],abs_tol=.000001) for k in NUTRIENTS)
            for k,gold in case.get('target_comparison',{}).items():
                assert math.isclose(gold['recorded']-gold['target'],gold['difference'],abs_tol=.000001)
                assert math.isclose(sum(r[k] for r in totals.values()),gold['recorded'],abs_tol=.000001)
            assert sorted((s['trained_at'],s['status']) for s in baseline['workout_sessions'])==sorted(tuple(x) for x in case.get('sessions',[]))
            if 'workout_counter_gold' in case:
                from app.services.agent_tools import build_read_tools
                gold = case['workout_counter_gold']
                async with factory() as db:
                    reads = build_read_tools(db, user_id=uid, allowlist=['plan.get_active','workout.get_progress','workout.list_history'])
                    plan_result = await reads[0].ainvoke({})
                    progress_result = await reads[1].ainvoke({'weeks':1})
                    history_result = await reads[2].ainvoke({'start_date':gold['week_start'],'end_date':gold['as_of'],'completed_only':True,'limit':20})
                assert plan_result['scope']['as_of'] == progress_result['as_of'] == gold['as_of']
                assert plan_result['plan']['week_start'] == gold['week_start']
                assert plan_result['plan']['weekly_completed_days'] == gold['plan_completed_days']
                expected_progress = sum(1 for day,status in case.get('sessions',[]) if gold['week_start'] <= day <= gold['as_of'] and status in ('completed','ended_early'))
                assert progress_result['total_sessions'] == expected_progress
                assert history_result['count'] == min(20, gold['history_sessions'])
                assert history_result['total_count'] == gold['history_sessions']
                assert history_result['truncated'] == (gold['history_sessions'] > 20)
                counter_preflight = {'gold':gold, 'plan_result':plan_result, 'progress_result':progress_result,'completed_history_result':history_result}
                if gold['history_sessions'] > 20:
                    async with factory() as db:
                        scoped = build_read_tools(db, user_id=uid, allowlist=['workout.get_progress'])[0]
                        exact = await scoped.ainvoke({'start_date':gold['week_start'],'end_date':gold['as_of'],'statuses':['completed']})
                    assert exact['aggregation'] == 'all_matching_sessions'
                    assert exact['total_sessions'] == gold['history_sessions']
                    assert exact['count_scope'] == {'start_date':gold['week_start'], 'end_date':gold['as_of'], 'requested_end_date':gold['as_of'], 'as_of':gold['as_of'], 'timezone':'Asia/Shanghai', 'statuses':['completed']}
                    counter_preflight['exact_completed_aggregate'] = exact
            if case['kind']=='nutrition_query' and not case.get('query_gold'):
                actual=[{'date':m['logged_at'],'calories':sum(x['calories'] for x in m['items'])} for m in v['meals'] if m['logged_at']<=BENCH['business_date']]
                by_date = {}
                for row in actual: by_date[row['date']] = by_date.get(row['date'], 0) + row['calories']
                assert [{'date':d,'calories':c} for d,c in sorted(by_date.items(),reverse=True)]==case['gold']
            for field, value in {**BENCH['profile'], **case.get('profile', {})}.items():
                if field!='diet_restriction': assert baseline['user_profiles'][0][field]==value
            assert baseline['user_profiles'][0]['diet_restriction']==case.get('diet_restriction','无')
            async def override():
                async with factory() as db: yield db
            app.dependency_overrides[get_db] = override
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic-test') as client:
                request_data={'conversation_id':cid,'message':case['turns'][0],'client_request_id':'round52-'+case['id']+'-r1-0'}
                headers={'Authorization':'Bearer '+create_access_token(uid)}
                response=await client.post('/api/v1/agent/runs',headers=headers,json=request_data)
                assert response.status_code in (200,202),response.text
                fetched=await client.get('/api/v1/agent/runs/'+response.json()['run_id'],headers=headers)
                assert fetched.status_code==200 and fetched.json()['status']=='queued'
            app.dependency_overrides.pop(get_db,None)
            assert await snapshot(factory)==baseline
            if case.get('query_gold'):
                from query_checks import fixture_rows
                for gold in case['query_gold']:
                    assert fixture_rows(case, gold['arguments']) == gold['rows']
            report['fixtures'].append({'case':case['id'],'schema':schema,'snapshot':baseline,'view':v})
            if counter_preflight is not None:
                report['fixtures'][-1]['counter_preflight'] = counter_preflight
            report['fixtures'][-1]['api_preflight']={'request':request_data,'status':response.status_code,'body':response.json(),'get_body':fetched.json(),'worker_processed':False}
            await engine.dispose()
        report['passed']=True; report['finished_at']=utc()
        dump(ROOT/'preflight.json',report); print('Preflight passed; model_calls=0',flush=True); return
    freeze = json.loads((ROOT/'freeze.json').read_text(encoding='utf-8'))
    for name, sha in freeze['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha, 'Frozen file changed: '+name
    assert settings.DEEPSEEK_API_KEY and settings.DEEPSEEK_BASE_URL.rstrip('/')=='https://api.deepseek.com'
    source_manifest = json.loads((ROOT/'candidate-source.json').read_text(encoding='utf-8'))
    for f in source_manifest['files']:
        assert hashlib.sha256((Path('/workspace')/f['file']).read_bytes()).hexdigest()==f['sha256'], 'Candidate changed: '+f['file']
    (ROOT/'runs').mkdir(exist_ok=True)
    startfile = ROOT/'live-start.json'
    fingerprint=hashlib.sha256((ROOT/'freeze.json').read_bytes()).hexdigest()
    if not startfile.exists():
        with startfile.open('x',encoding='utf-8') as f:json.dump({'started_at':utc(),'business_scene':BENCH['business_date'],'run_calendar_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),'freeze_sha256':fingerprint},f)
    else:assert json.loads(startfile.read_text())['freeze_sha256']==fingerprint
    BUDGET=Budget(ROOT/'live-budget.json',fingerprint,seconds=BENCH['limits']['suite_seconds'],runs=BENCH['limits']['max_runs'],requests=BENCH['limits']['max_provider_requests'],max_estimated_cny=BENCH['limits']['max_estimated_cny'])
    CAPTURE.budget=BUDGET;CAPTURE.count=BUDGET.state['requests'];RUN_COUNT=BUDGET.state['runs']
    from recover import recover_started
    from session_registry import Registry
    REGISTRY=Registry(ROOT,freeze=fingerprint,limits=BUDGET.state['limits'],prefix=SCHEMA_PREFIX,expected_turns={c['id']+'-r'+str(rep):len(c['turns']) for c in BENCH['cases'] for rep in range(1,BENCH['repeats']+1)})
    await recover_started(BUDGET,REGISTRY)
    from cost_budget import reconcile_costs
    reconcile_costs(BUDGET,ROOT)
    settings_log = {k:v for k,v in settings.model_dump().items() if k.startswith('AGENT_') and 'KEY' not in k and 'DATABASE' not in k}
    dump(ROOT/'effective-settings.json', settings_log)
    SUITE_START = time.monotonic(); CAPTURE.started = SUITE_START; recs=[]
    for group in dict.fromkeys(c['group'] for c in BENCH['cases']):
        for repeat in range(1,BENCH['repeats']+1):
            for case in [c for c in BENCH['cases'] if c['group']==group]:
                instance=case['id']+'-r'+str(repeat)
                if instance in BENCH['carried_instances']:continue
                if instance in BUDGET.state['instances']:continue
                try:BUDGET.check()
                except EvaluationStopped:break
                if CAPTURE.fatal or CAPTURE.count>=BENCH['limits']['max_provider_requests'] or RUN_COUNT>=BENCH['limits']['max_runs']: break
                recs.append(await run_case(case,repeat))

    recs=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((ROOT/'runs').glob('*.json'))]
    dump(ROOT/'results-first.json', {'benchmark':BENCH['id'],'finished_at':utc(),'instances':[{'id':r['instance'],'score':r.get('score'),'error':r.get('error'),'classification':r.get('classification')} for r in recs], 'provider_requests':CAPTURE.count, 'planned_instances':len(BENCH['cases'])*BENCH['repeats'], 'executed_instances':len(recs), 'semantic_review':'pending', 'execution_summary':summarize_batch(recs)})

if __name__=='__main__':
    from durable_budget import file_lock
    with file_lock(ROOT/'runner.lock',blocking=False):
        asyncio.run(main())
