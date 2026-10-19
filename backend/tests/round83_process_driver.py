"""Only synthetic local HTTP; parent kills this process at recorded barriers."""
import asyncio,json,os,sys,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import AsyncMock
import httpx
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from sqlalchemy.pool import NullPool
from app.config import settings
from app.models.agent import AgentRun
from app.services import agent_runtime as runtime,agent_tools
from app.services.agent_jobs import process_agent_run
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_model_usage import ObservedAsyncClient
from test_round76_live_evidence import ROWS,resolution,tool_calls,GOOD

schema,rid,mode,destination=sys.argv[1:]
D=Path(destination);D.mkdir(parents=True,exist_ok=True)
events=[]
def record(kind,**values):
    events.append({'kind':kind,**values})
    (D/'events.json').write_text(json.dumps(events,ensure_ascii=True,indent=2))
def cut(where):
    record('barrier',where=where)
    (D/'barrier').write_text(where)
    threading.Event().wait(60)
    os._exit(78)

class Provider(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        n=sum(x['kind']=='request' for x in events)
        record('request',body=body)
        if mode=='http_pending' or (mode=='later_http_pending' and n==1):cut('provider received request, before response')
        message=tool_calls('plan_get_active','health_get_screening_summary') if n==0 else {'role':'assistant','content':GOOD}
        reply={'id':'synthetic-r83','object':'chat.completion','model':'synthetic',
               'choices':[{'index':0,'finish_reason':'tool_calls' if n==0 else 'stop','message':message}],
               'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}}
        raw=json.dumps(reply).encode();record('response',body=reply)
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
        self.wfile.write(raw);self.wfile.flush()

server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
threading.Thread(target=server.serve_forever,daemon=True).start()
settings.DEEPSEEK_API_KEY='synthetic-loopback-only'
settings.DEEPSEEK_BASE_URL=f'http://127.0.0.1:{server.server_port}'
settings.AGENT_ENABLED=True;settings.AGENT_PLANNED_EXECUTION_ENABLED=False
settings.AGENT_RUN_LEASE_SECONDS=120
r=resolution(ROWS['SV01-r1']['turns'][0]['body'])
runtime.resolve_intent_with_fallback=AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1))
send=ObservedAsyncClient.send
async def observed_send(self,*args,**kw):
    result=await send(self,*args,**kw)
    if mode=='model_done':cut('model response observed, before SDK dispatch')
    return result
ObservedAsyncClient.send=observed_send
serialize=agent_tools._serialize_session_read
def serial(coro,db):
    async def inner(*a,**kw):
        if mode=='tool_started':cut('tool intent committed, before SQL read')
        result=await coro(*a,**kw);record('actual_tool',name=coro.__name__,result=result)
        if mode=='tool_uncheckpointed':cut('tool returned, before result checkpoint')
        return result
    return serialize(inner,db)
agent_tools._serialize_session_read=serial
builder=runtime.build_read_tools
def build(*a,**kw):
    result=builder(*a,**kw)
    for tool in result:
        original=tool.coroutine
        async def invoke(*args,_original=original,**kwargs):
            value=await _original(*args,**kwargs)
            if mode=='tool_done':cut('tool result committed, before final answer')
            return value
        tool.coroutine=invoke
    return result
runtime.build_read_tools=build
if mode=='http_headers':
    from app.services.agent_attempt_journal import AttemptJournal
    save=AttemptJournal.save
    async def saved(self,**kw):
        await save(self,**kw)
        ledger=kw.get('ledger')
        if ledger and ledger.calls and ledger.calls[-1].responses and ledger.calls[-1].outcome=='pending':
            cut('HTTP headers committed, body unconsumed')
    AttemptJournal.save=saved
commit=AsyncSession.commit
async def committed(self):
    if mode=='before_commit' and any(isinstance(x,AgentRun) and x.status=='completed' for x in self.identity_map.values()):
        cut('final answer staged, before business commit')
    return await commit(self)
AsyncSession.commit=committed

async def main():
    engine=create_async_engine(os.environ['AGENT_QUERY_EVAL_DATABASE_URL'],poolclass=NullPool,
        connect_args={'server_settings':{'search_path':schema+',public'}})
    try:await process_agent_run(async_sessionmaker(engine,expire_on_commit=False),rid)
    finally:await engine.dispose()
asyncio.run(main())
record('process_completed')
