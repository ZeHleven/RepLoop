import asyncio,copy,json,os,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest,pytest_asyncio,httpx
from sqlalchemy import select,text,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.models.agent import AgentRun,AgentMessage,AgentConversation,AgentProposal,AgentAttemptJournal
from app.models.meal import MealLog
from app.services.auth import create_access_token
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.agent_task_state import load_task_snapshot,advance_task_state,finalize_task_snapshot
from app.schemas.agent_task import TaskUpdate
from test_agent_domain_proposals import _context
from round85_support import configure,install_route,install_transport,update

MATRIX=json.loads((Path(__file__).parent/'fixtures/round85/task-matrix.json').read_text(encoding='utf8'))
CASES=MATRIX['m4'][:20]
@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def destination(case,tmp_path):
    d=Path(os.getenv('R85_EVIDENCE_DIR',str(tmp_path)))/('state-'+case+'-'+uuid4().hex);d.mkdir(parents=True);return d
def write(path,value):path.write_text(json.dumps(value,default=str,ensure_ascii=False,indent=2),encoding='utf8')

@pytest.mark.asyncio
@pytest.mark.parametrize('case',CASES,ids=[c['id'] for c in CASES])
async def test_latest_sourced_requirement_survives_failed_run(db_session,session_factory,monkeypatch,tmp_path,case):
    user,_,conv,_=await _context(db_session,'r85-'+uuid4().hex);d=destination(case['id'],tmp_path)
    schema=await db_session.scalar(text('select current_schema()'))
    configure(monkeypatch);command={};contexts=[];requests=[];install_route(monkeypatch,command,contexts);install_transport(monkeypatch,command,requests)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    evidence={'case':case,'turns':[],'scripted_model_only':True,'business_success':None};aliases={}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            async def turn(message,patch,fail=False,fresh=False):
                command.clear();command.update(update=patch,fail=fail);start=len(contexts);request_start=len(requests)
                posted=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
                assert posted.status_code==202,posted.text
                rid=posted.json()['run_id']
                async with session_factory() as db:assert await claim_next_agent_run(db)==rid
                process=None
                if fresh:
                    f=d/(rid+'-config.json');write(f,command);target=d/rid
                    proc=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('round85_process_driver.py')),schema,rid,str(f),str(target),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    try:stdout,stderr=await asyncio.wait_for(proc.communicate(),40)
                    finally:
                        if proc.returncode is None:proc.kill();await proc.wait()
                    (d/(rid+'-stderr.txt')).write_bytes(stderr);assert proc.returncode==0,stderr.decode()[-1800:]
                    process=json.loads((target/'observed.json').read_text())
                    seen=process['contexts'];sent=process['requests']
                else:
                    await asyncio.wait_for(process_agent_run(session_factory,rid),30);seen=contexts[start:];sent=requests[request_start:]
                body=(await client.get('/api/v1/agent/runs/'+rid)).json()
                journal=(await client.get('/api/v1/agent/runs/'+rid+'/attempts')).json()
                entry=dict(message=message,command=copy.deepcopy(command),body=body,journal=journal,contexts=seen,requests=sent,process=process)
                evidence['turns'].append(entry);write(d/'result.json',evidence)
                assert body['status']==('failed' if fail else 'completed'),body
                assert seen and sent,'Must reach actual route context and SDK request'
                return entry
            first=await turn(case['turns'][0],update('new',case['turns'][0],**{case['requirement_key']:case['initial']}));tid=first['body']['id']
            aliases['primary']=tid
            for n in range(case['insert_after_initial']['neutral_turns']):
                message=f'这个讨论先继续，第{n+1}轮不改条件。'
                await turn(message,update('none',message))
            if case['category']=='switching':
                m='插入一个独立问题：解释呼吸节奏。';b=await turn(m,update('new',m,topic='解释呼吸节奏'));bid=b['body']['id']
                for task,message in [(tid,'回到刚才的主要任务。'),(bid,'返回呼吸节奏问题。'),(tid,'再次回到主要任务。')]:
                    await turn(message,update('resume',message,task))
            changed=await turn(case['turns'][1],update('continue',case['turns'][1],tid,**{case['requirement_key']:case['replacement']}),fail=True,fresh=case['restart'])
            resumed=await turn(case['turns'][2],update('continue',case['turns'][2],tid),fresh=case['restart'])
            failed_task=next(t for t in changed['body']['execution_trace']['task_state']['tasks'] if t['id']==tid)
            last_task=next(t for t in resumed['body']['execution_trace']['task_state']['tasks'] if t['id']==tid)
            requirement=next(v for v in last_task['requirements'] if v['key']==case['requirement_key'])
            inherited=json.loads(next(x['content'] for x in resumed['contexts'][0]['context'] if x['role']=='task_state'))
            evidence['observed']={'failed_phase':failed_task['phase'],'last_requirement':requirement,'inherited':inherited}
            if case['restart']:
                assert changed['process']['pid']!=resumed['process']['pid']
            if case['category']=='long_window':
                history=[x for x in resumed['contexts'][0]['context'] if x['role'] in ('user','assistant')]
                assert len(history)<=20 and not any(x['content']==case['turns'][0] for x in history)
            async with session_factory() as db:
                assert await db.scalar(select(func.count()).select_from(MealLog).where(MealLog.user_id==user.id))==0
                assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
                assert await db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.run_id==changed['body']['id'],AgentMessage.role=='assistant'))==0
            write(d/'result.json',evidence)
            assert requirement['quote']==case['replacement'],evidence['observed']
            assert requirement['source_run_id']==changed['body']['id']
            assert failed_task['phase']=='failed',evidence['observed']
            assert any(case['replacement'] in json.dumps(x,ensure_ascii=False) for x in resumed['requests'])
            evidence['mechanism_passed']=True;write(d/'result.json',evidence)
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['cancel','invalid_source','eviction','isolation'])
async def test_task_boundaries_do_not_revive_or_cross_scope(db_session,session_factory,mode,tmp_path):
    user,_,conv,_=await _context(db_session,'r85-boundary-'+uuid4().hex);other,_,otherconv,_=await _context(db_session,'r85-foreign-'+uuid4().hex)
    message='只建议不要保存';state=advance_task_state(None,TaskUpdate.model_validate(update('new',message,permission=message)),message=message,run_id='task-original',normalized_request=message)
    state=finalize_task_snapshot(state,terminal_action='answer')
    if mode=='cancel':
        state=advance_task_state(state,TaskUpdate.model_validate(update('cancel','取消','task-original')),message='取消',run_id='cancel-run',normalized_request='取消')
    elif mode=='eviction':
        for n in range(3):state=advance_task_state(state,TaskUpdate.model_validate(update('new','新问题')),message='新问题',run_id='other-'+str(n),normalized_request='新问题')
    valid=AgentRun(user_id=user.id,conversation_id=conv.id,status='completed',queue_position=1,execution_trace={'task_state':state.model_dump(mode='json')});db_session.add(valid)
    current=AgentRun(user_id=user.id,conversation_id=conv.id,status='queued',queue_position=10);db_session.add(current)
    if mode=='invalid_source':
        invalid=AgentRun(user_id=user.id,conversation_id=conv.id,status='failed',queue_position=2,execution_trace={});db_session.add(invalid)
    if mode=='isolation':
        extra=AgentConversation(user_id=user.id);db_session.add(extra);await db_session.flush()
        for uid,cid,pos,status in [(other.id,otherconv.id,9,'completed'),(user.id,extra.id,8,'failed'),(user.id,conv.id,11,'completed'),(user.id,conv.id,7,'running'),(user.id,conv.id,6,'queued')]:
            db_session.add(AgentRun(user_id=uid,conversation_id=cid,queue_position=pos,status=status,execution_trace={'task_state':{'version':1,'active_task_id':None,'tasks':[]}}))
    await db_session.commit()
    out=await load_task_snapshot(db_session,current)
    d=destination('boundary-'+mode,tmp_path);write(d/'result.json',{'mode':mode,'expected':state.model_dump(mode='json'),'actual':out.model_dump(mode='json')})
    assert out==state
    if mode in {'cancel','eviction'}:
        with pytest.raises(ValueError,match='unavailable'):
            advance_task_state(out,TaskUpdate.model_validate(update('resume','返回原任务','task-original')),message='返回原任务',run_id='bad',normalized_request='返回')

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['new','resume','cancel'])
async def test_failed_valid_transition_is_latest_even_without_business_answer(db_session,action,tmp_path):
    user,_,conv,_=await _context(db_session,'r85-transition-'+uuid4().hex)
    def apply(state,kind,message,rid,tid=''):
        return advance_task_state(state,TaskUpdate.model_validate(update(kind,message,tid)),message=message,run_id=rid,normalized_request=message)
    state=apply(None,'new','讨论训练安排','original')
    state=apply(state,'new','讨论饮食安排','other')
    old=AgentRun(user_id=user.id,conversation_id=conv.id,status='completed',queue_position=1,execution_trace={'task_state':state.model_dump(mode='json')});db_session.add(old)
    if action=='new':state=apply(state,'new','现在讨论下周时间','fresh')
    elif action=='resume':state=apply(state,'resume','返回训练安排','return','original')
    else:state=apply(state,'cancel','取消饮食讨论','cancel','other')
    state=finalize_task_snapshot(state,terminal_action='failed')
    db_session.add(AgentRun(user_id=user.id,conversation_id=conv.id,status='failed',queue_position=2,execution_trace={'task_state':state.model_dump(mode='json')}))
    current=AgentRun(user_id=user.id,conversation_id=conv.id,status='queued',queue_position=3);db_session.add(current);await db_session.commit()
    actual=await load_task_snapshot(db_session,current)
    d=destination('transition-'+action,tmp_path);write(d/'result.json',dict(action=action,expected=state.model_dump(mode='json'),actual=actual.model_dump(mode='json')))
    assert actual==state

@pytest.mark.asyncio
@pytest.mark.parametrize('trace',[None,{}, {'task_state':None}])
async def test_failure_before_validated_transition_keeps_prior_state(db_session,trace):
    user,_,conv,_=await _context(db_session,'r85-invalid-'+uuid4().hex)
    message='只建议不要保存';state=advance_task_state(None,TaskUpdate.model_validate(update('new',message,permission=message)),message=message,run_id='original',normalized_request=message)
    db_session.add_all([AgentRun(user_id=user.id,conversation_id=conv.id,status='completed',queue_position=1,execution_trace={'task_state':state.model_dump(mode='json')}),
        AgentRun(user_id=user.id,conversation_id=conv.id,status='failed',queue_position=2,execution_trace=trace)])
    current=AgentRun(user_id=user.id,conversation_id=conv.id,status='queued',queue_position=3);db_session.add(current);await db_session.commit()
    assert await load_task_snapshot(db_session,current)==state
