"""Eval-only attempt recorder: wrap before Pydantic parsing, preserve exceptions."""
import asyncio, copy, math, time, json
from uuid import uuid4
from langchain_core.messages import ToolMessage
from contextvars import ContextVar
from langchain_core.tools import BaseTool
from pydantic import ValidationError
from app.services.agent_tools import TOOL_ID_BY_LANGCHAIN_NAME
ERROR_STATUSES = {'error', 'failed', 'failure', 'cancelled', 'timeout'}

def returned_error(result, tool_message=None):
    """Only the response envelope is an error contract, never nested business rows."""
    if isinstance(tool_message, dict) and tool_message.get('status') == 'error':
        return 'tool_message_error'
    if not isinstance(result, dict):
        return None
    if isinstance(result.get('status'), str) and result['status'] in ERROR_STATUSES:
        return 'payload_status_error'
    for key in ('success', 'ok'):
        if result.get(key) is False:
            return 'payload_' + key + '_false'
    for key in ('error', 'error_code', 'error_type', 'exception'):
        if result.get(key):
            return 'payload_' + key
    return None



CONTEXT=ContextVar('agent_evaluation_context',default={})
def upsert_attempt(rows,row):
    """Sink receives lifecycle events; one persisted row per logical invocation."""
    snapshot=copy.deepcopy(row)
    index=next((i for i,old in enumerate(rows) if old['attempt_id']==row['attempt_id']),None)
    if index is None:rows.append(snapshot)
    else:rows[index]=snapshot
def quantiles(values):
    x=sorted(values)
    return {'n':len(x),**{k:(x[math.ceil(p*len(x))-1] if x else None) for k,p in [('p50',.5),('p95',.95),('p99',.99),('max',1)]}}

class ToolRecorder:
    def __init__(self,sink): self.sink=sink;self.original=None
    def install(self):
        assert self.original is None
        self.original=BaseTool.ainvoke;original=self.original;recorder=self
        async def wrapped(tool,input,*args,**kwargs):
            if tool.name not in TOOL_ID_BY_LANGCHAIN_NAME:return await original(tool,input,*args,**kwargs)
            row={**copy.deepcopy(CONTEXT.get()),'attempt_id':uuid4().hex,'tool':TOOL_ID_BY_LANGCHAIN_NAME[tool.name],'arguments':copy.deepcopy(input),'status':'started','lifecycle_complete':False,'started_wall':time.time()}
            start=time.monotonic()
            # This event must reach durable storage before validation or tool code.
            recorder.sink(copy.deepcopy(row))
            try:
                result=await original(tool,input,*args,**kwargs)
                row.update(status='success',result=copy.deepcopy(result))
                if isinstance(result,ToolMessage):
                    row['tool_message']=result.model_dump(mode='json')
                    try:row['result']=json.loads(result.content)
                    except (TypeError,ValueError):row['result']={'content':result.content}
                error_contract=returned_error(row.get('result'),row.get('tool_message'))
                if error_contract:row.update(status='error',category='returned_error',error_contract=error_contract)
                return result
            except BaseException as exc:
                category='schema' if isinstance(exc,ValidationError) else 'timeout' if isinstance(exc,(TimeoutError,asyncio.TimeoutError)) else 'cancelled' if isinstance(exc,asyncio.CancelledError) else 'execution'
                row.update(status='error',category=category,error_type=type(exc).__name__);raise
            finally:
                row.update(lifecycle_complete=True,finished_wall=time.time(),elapsed_ms=round((time.monotonic()-start)*1000,3));recorder.sink(copy.deepcopy(row))
        BaseTool.ainvoke=wrapped
    def uninstall(self):
        if self.original:BaseTool.ainvoke=self.original;self.original=None
