"""Script only model output; the production API/worker owns all state changes."""
import copy,importlib,json
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolution,IntentResolverOutcome
from app.schemas.agent_task import TaskUpdate
from app.config import settings

MARKER='合成执行标记；本回答不用于业务质量评分。'

def configure(monkeypatch=None):
    for key,value in dict(AGENT_ENABLED=True,AGENT_PLANNED_EXECUTION_ENABLED=False,AGENT_ASYNC_WORKER_ENABLED=False,
            DEEPSEEK_API_KEY='synthetic-r85-only',DEEPSEEK_BASE_URL='https://api.deepseek.com').items():
        if monkeypatch is None:setattr(settings,key,value)
        else:monkeypatch.setattr(settings,key,value)

def install_route(monkeypatch,command,observed):
    async def resolve(message,**kwargs):
        observed.append({'message':message,'context':copy.deepcopy(kwargs.get('context_messages'))})
        return IntentResolverOutcome(resolution=IntentResolution(primary_intent='general_qa',intent_domain='general',request_kind='query',requested_effect='read',
            requested_output='answer',resolved_query=message,risk_level='low',confidence=1,
            task_update=TaskUpdate.model_validate(command['update'])),source='model',attempt_count=1)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',resolve)

def install_transport(monkeypatch,command,requests):
    for name in ('httpx','httpx2'):
        module=importlib.import_module(name);original=module.AsyncClient.send
        async def send(client,request,_module=module,_original=original,**kw):
            if request.url.host=='test':return await _original(client,request,**kw)
            assert request.url.host=='api.deepseek.com','Synthetic test cannot call any external host'
            requests.append(json.loads(request.content))
            if command.get('fail'):
                return _module.Response(503,json={'error':{'message':'synthetic R85 service failure','type':'service_unavailable'}},request=request)
            return _module.Response(200,json=reply(),request=request)
        monkeypatch.setattr(module.AsyncClient,'send',send)

def reply():
    return {'id':'r85-synthetic','object':'chat.completion','model':'synthetic','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':MARKER}}],
            'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}}

def update(action,message,task_id='',**requirements):
    return dict(action=action,task_id=task_id,trigger=message if action!='none' else '',requirements=[dict(key=k,quote=v,remove=False) for k,v in requirements.items()])
