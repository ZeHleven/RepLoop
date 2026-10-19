"""Keep provider labels separate from unique per-run read execution identity.

Only response IDs change, before the graph dispatches tools. Names/arguments,
usage, and provider response metadata remain untouched. This is not result
caching: two requests with the same provider label are still two attempts.
"""
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage,ToolMessage


def _sha(value):return hashlib.sha256(value.encode('utf8',errors='surrogatepass')).hexdigest()
def _invalid_characters(value):return isinstance(value,str) and any(c=='\x00' or 0xD800<=ord(c)<=0xDFFF for c in value)
def _valid(value):return isinstance(value,str) and bool(value.strip()) and len(value)<=120 and not _invalid_characters(value)


class ReadToolIdentity(AgentMiddleware):
    def __init__(self,messages):
        self.used=set();self.changes=[];self.generation=0
        for message in messages:
            if isinstance(message,ToolMessage):self.used.add(message.tool_call_id)
            for call in getattr(message,'tool_calls',[]) or []:
                if isinstance(call.get('id'),str):self.used.add(call['id'])

    def normalize(self,response):
        self.generation+=1
        reserved=self.used|{c['id'] for m in response.result for c in getattr(m,'tool_calls',[]) or [] if _valid(c.get('id'))}
        result=[]
        for position,message in enumerate(response.result):
            if not isinstance(message,AIMessage) or not message.tool_calls:
                result.append(message);continue
            calls=[];changed=False
            for index,call in enumerate(message.tool_calls):
                original=call.get('id')
                reason=('missing' if original is None else 'blank' if isinstance(original,str) and not original.strip()
                        else 'invalid_characters' if _invalid_characters(original)
                        else 'too_long' if isinstance(original,str) and len(original)>120
                        else 'duplicate' if original in self.used else None)
                if reason is None:
                    self.used.add(original);calls.append(call.copy());continue
                # A bounded collision check also reserves later labels in this
                # same response; no unbounded retry or extra model request.
                for _ in range(8):
                    identity='read-'+uuid4().hex
                    if identity not in reserved:break
                else:raise RuntimeError('read_tool_identity_allocation_failed')
                reserved.add(identity);self.used.add(identity)
                calls.append({**call,'id':identity});changed=True
                self.changes.append({'generation':self.generation,'response_index':position,'call_index':index,
                    'tool_name':call['name'],'execution_id':identity,'reason':reason,
                    'provider_id':(json.dumps(original[:4096],ensure_ascii=True) if _invalid_characters(original) else original[:4096]) if isinstance(original,str) else None,
                    'provider_id_encoding':'json_string' if _invalid_characters(original) else 'literal',
                    'provider_id_length':len(original) if isinstance(original,str) else None,
                    'provider_id_sha256':_sha(original) if isinstance(original,str) else None,
                    'provider_id_truncated':isinstance(original,str) and len(original)>4096,
                    'arguments_sha256':_sha(json.dumps(call['args'],ensure_ascii=False,sort_keys=True,separators=(',',':')))})
            # Keep original raw additional_kwargs for provenance. Chat model
            # serialization uses the normalized parsed tool_calls field.
            result.append(message.model_copy(update={'tool_calls':calls},deep=True) if changed else message)
        return replace(response,result=result)

    async def awrap_model_call(self,request,handler):
        return self.normalize(await handler(request))

    def receipt(self):
        return {'version':1,'scope':'read_graph_run','changes':[dict(c) for c in self.changes]} if self.changes else None
