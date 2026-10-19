"""Real runner/SQL plus killed child process; all provider activity is scripted."""
import asyncio,hashlib,json,os,sys,time
from datetime import date,datetime,timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from durable_budget import Budget,EvaluationStopped,atomic_json,file_lock
from session_registry import Registry

HERE=Path('/evidence')

def save(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,default=str)


def configure(root):
    import run as runner
    from app.config import settings
    settings.AGENT_ENABLED=True;settings.AGENT_ASYNC_WORKER_ENABLED=False
    runner.ROOT=root;runner.BUSINESS_DATE.set(date.fromisoformat(runner.BENCH['business_date']))
    freeze=hashlib.sha256((root/'freeze.json').read_bytes()).hexdigest()
    runner.BUDGET=Budget(root/'budget.json',freeze,seconds=180,runs=10,requests=10,max_estimated_cny=runner.BENCH['limits']['max_estimated_cny'])
    runner.RUN_COUNT=runner.BUDGET.state['runs']
    runner.REGISTRY=Registry(root,freeze=freeze,limits=runner.BUDGET.state['limits'],prefix=runner.SCHEMA_PREFIX,expected_turns={'WX24-r1':1,'WX24-r2':1})
    return runner


async def child_pending(root):
    runner=configure(root)
    async def pending(factory,rid):
        # Simulates a reserved pending provider request. No HTTP provider call.
        runner.BUDGET.reserve('requests')
        runner.CURRENT['provider'].append(dict(index=1,fixture='synthetic_pending_no_network',lifecycle_complete=False,complete=False,response=None))
        runner.save_current()
        print('RUNNER_WORKER_ENTERED '+rid,flush=True)
        await asyncio.sleep(600)
    runner.process_agent_run=pending
    with file_lock(root/'runner.lock',blocking=False):
        await runner.run_case(next(c for c in runner.BENCH['cases'] if c['id']=='WX24'),2)


async def subprocess_run(code,*args):
    proc=await asyncio.create_subprocess_exec(sys.executable,'-c',code,*map(str,args),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await asyncio.wait_for(proc.communicate(),30)
    return proc.returncode,out.decode(),err.decode()


async def main():
    assert not os.environ.get('DEEPSEEK_API_KEY')
    checks=[];root=HERE/'harness-attempts'/uuid4().hex;root.mkdir(parents=True)
    (root/'runs').mkdir();save(root/'freeze.json',{'synthetic':'actual runner boundary, no live model'})
    runner=configure(root)
    from app.models.agent import AgentRun,AgentMessage
    async def completed(factory,rid):
        async with factory() as db:
            run=await db.get(AgentRun,rid);run.status='completed';run.completed_at=datetime.now(timezone.utc)
            db.add(AgentMessage(run_id=rid,conversation_id=run.conversation_id,role='assistant',content='Scripted worker completion, not a business pass.'))
            await db.commit()
    with patch.object(runner,'process_agent_run',completed):
        rec=await runner.run_case(next(c for c in runner.BENCH['cases'] if c['id']=='WX24'),1)
    assert rec['turns'][0]['body']['status']=='completed' and rec['baseline']==rec['final']
    assert runner.BUDGET.state['instances']['WX24-r1']=='finished'
    identity=json.loads((root/'identity-registry/WX24-r1.json').read_text())
    assert identity['schema']==rec['schema'] and identity['user_id']==rec['user_id']
    checks.append('actual runner registers independent scope, submits/claims/reads one scripted completed Run; business unchanged')
    before=json.loads((root/'budget.json').read_text())
    proc=await asyncio.create_subprocess_exec(sys.executable,'/evidence/test_harness.py','child-pending',str(root),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    async def ready():
        lines=[]
        while True:
            line=await proc.stdout.readline()
            if not line:raise AssertionError('Child exited before worker boundary: '+(await proc.stderr.read()).decode())
            lines.append(line.decode())
            if line.startswith(b'RUNNER_WORKER_ENTERED '):return lines
    output=await asyncio.wait_for(ready(),30)
    assert proc.returncode is None
    pending_bytes=(root/'runs/WX24-r2.json').read_bytes()
    assert not json.loads(pending_bytes).get('finished_at')
    proc.kill();rest,stderr=await asyncio.wait_for(proc.communicate(),10)
    assert proc.returncode==-9
    save(root/'child-kill.json',dict(pid=proc.pid,exit=proc.returncode,ready=output,remaining_stdout=rest.decode(),stderr=stderr.decode(),provider_network_calls=0))
    resumed=configure(root);state=json.loads((root/'budget.json').read_text())
    assert state['deadline']==before['deadline'] and state['started']==before['started'] and state['clock_epoch']==before['clock_epoch']
    assert state['runs']==2 and state['requests']==1 and state['instances']['WX24-r2']=='started'
    checks.append('SIGKILL after actual runner submit/claim and synthetic request reservation; new process preserves original deadline/counters/started slot')
    from recover import recover_started
    budget_before=(root/'budget.json').read_bytes()
    path,report=await recover_started(resumed.BUDGET,resumed.REGISTRY)
    assert (root/'budget.json').read_bytes()==budget_before and (root/'runs/WX24-r2.json').read_bytes()==pending_bytes
    item=report['instances'][0]
    assert item['instance']=='WX24-r2' and item['classification']=='unresolved_in_flight'
    assert item['sql_observation']['runs'][0]['status']=='running' and item['local']['provider_pending']==1
    save(root/'recovery-reference.json',dict(report=str(path),budget_sha256=hashlib.sha256(budget_before).hexdigest(),raw_sha256=hashlib.sha256(pending_bytes).hexdigest()))
    checks.append('actual runner recovery adapter observes SQL running and pending provider; no raw/budget rewrite or worker replay')
    try:resumed.BUDGET.begin('WX24-r2')
    except EvaluationStopped as exc:assert 'already started' in str(exc)
    else:raise AssertionError('Killed instance replayed')
    checks.append('started instance cannot be replayed after new Budget construction')
    # File lock is released by OS after process death, but prevents a second
    # live runner from entering while another process owns the session.
    with file_lock(root/'runner.lock',blocking=False):
        code="from durable_budget import file_lock; import sys;\nwith file_lock(sys.argv[1],blocking=False): print('entered')"
        rc,out,err=await subprocess_run(code,root/'runner.lock')
        assert rc!=0 and 'entered' not in out and 'BlockingIOError' in err
    checks.append('exclusive runner lock rejects live second process and recovers automatically after killed owner')
    # Budget is now expired by explicit clock injection, not by rewriting it.
    s=resumed.BUDGET.state
    stopped=Budget(root/'budget.json',s['freeze'],**s['limits'],wall=lambda:s['deadline']+1,mono=lambda:s['last_mono']+(s['deadline']+1-s['last_wall']),mono_source=s['clock_policy']['elapsed_clock'],epoch=lambda:s['clock_epoch'])
    try:stopped.check()
    except EvaluationStopped as exc:assert str(exc)=='wall_deadline'
    else:raise AssertionError('Expired budget accepted')
    b=(root/'budget.json').read_bytes();raw=(root/'runs/WX24-r2.json').read_bytes()
    _,expired=await recover_started(stopped,resumed.REGISTRY)
    assert (root/'budget.json').read_bytes()==b and (root/'runs/WX24-r2.json').read_bytes()==raw
    assert expired['instances'][0]['classification']=='unresolved_in_flight'
    checks.append('durable deadline stop still permits actual read-only recovery, without resetting budget')
    ident=root/'identity-registry/WX24-r2.json';original=ident.read_bytes()
    fake=json.loads(original);fake['user_id']='foreign';atomic_json(ident,fake);save(root/'tampered-identity.json',fake)
    try:await recover_started(stopped,resumed.REGISTRY)
    except RuntimeError as exc:assert 'fingerprint mismatch' in str(exc)
    else:raise AssertionError('Identity tamper accepted')
    ident.write_bytes(original)
    checks.append('independent registry modification rejected before SQL observation')
    # Multiprocess reservations must not lose increments or exceed the cap.
    concurrent=root/'concurrent-budget.json';Budget(concurrent,'concurrent',seconds=120,runs=20,requests=7)
    code="from durable_budget import Budget,EvaluationStopped; import sys,json; b=Budget(sys.argv[1],'concurrent',seconds=120,runs=20,requests=7); out=[]\nfor _ in range(3):\n try:out.append(b.reserve('requests'))\n except EvaluationStopped:out.append('stopped')\nprint(json.dumps(out))"
    workers=await asyncio.gather(*(subprocess_run(code,concurrent) for _ in range(4)))
    values=[]
    for rc,out,err in workers:assert rc==0,err;values+=json.loads(out)
    assert sorted(v for v in values if isinstance(v,int))==list(range(1,8)) and values.count('stopped')==5
    assert json.loads(concurrent.read_text())['requests']==7
    save(root/'concurrent-reservations.json',dict(values=values,processes=4,attempts=12,reserved=7,rejected=5))
    checks.append('4 actual processes reserve 12 times: exactly 7 unique requests consumed, 5 rejected, no lost updates')
    # Sticky boot/clock stops and watchdog cancellation use deterministic clocks.
    clock=[100.,50.];kw=dict(seconds=100,runs=2,requests=3,wall=lambda:clock[0],mono=lambda:clock[1],epoch=lambda:'boot-A')
    boot=Budget(root/'boot.json','f',**kw)
    switched=Budget(root/'boot.json','f',**{**kw,'epoch':lambda:'boot-B'})
    try:switched.check()
    except EvaluationStopped as exc:assert str(exc)=='clock_epoch_changed'
    else:raise AssertionError('Changed boot accepted')
    try:Budget(root/'boot.json','f',**kw).check()
    except EvaluationStopped as exc:assert str(exc)=='clock_epoch_changed'
    else:raise AssertionError('Sticky boot stop reset')
    checks.append('changed boot identity stops durably even after original identity returns')
    from eval_support import guarded_wait
    watch=Budget(root/'watchdog.json','f',**kw);cancelled=[]
    async def work():
        try:clock[0]+=60;clock[1]+=1;await asyncio.sleep(20)
        finally:cancelled.append(True)
    try:await guarded_wait(work(),watch,10)
    except EvaluationStopped as exc:assert str(exc)=='cumulative_clock_discontinuity'
    else:raise AssertionError('Watchdog did not stop')
    assert cancelled and json.loads(watch.path.read_text())['stop']=='cumulative_clock_discontinuity'
    checks.append('actual guarded_wait cancels pending coroutine on clock gap and persists stop')
    # Application/business snapshots in the killed schema also remain unchanged.
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from sqlalchemy.pool import NullPool
    from evals.postgres_test_fixtures import query_eval_database_url
    rec=json.loads(pending_bytes)
    e=create_async_engine(query_eval_database_url(),poolclass=NullPool,connect_args={'server_settings':{'search_path':rec['schema'],'default_transaction_read_only':'on'}})
    try:assert await runner.snapshot(async_sessionmaker(e,expire_on_commit=False))==rec['baseline']
    finally:await e.dispose()
    checks.append('killed task business snapshot remains unchanged after all read-only observations')
    result=dict(at=datetime.now(timezone.utc).isoformat(),passed=True,checks=checks,root=str(root),real_sql_runs=2,real_provider_network_calls=0,scripted_provider_pending=1,business_score=None,scope='Actual eval runner boundaries; scripted worker and deliberate kill, not real model business recovery')
    save(root/'results.json',result);save(HERE/f'harness-results-{uuid4().hex}.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='child-pending':asyncio.run(child_pending(Path(sys.argv[2])))
    else:asyncio.run(main())
