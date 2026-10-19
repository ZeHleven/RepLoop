"""Transparent eval-only recorder for httpx and httpx2. Never records headers."""
import importlib
import json
import time
from datetime import datetime, timezone

class ProviderCapture:
    def __init__(self, sink, *, max_requests=200, max_seconds=2700):
        self.sink=sink; self.max_requests=max_requests; self.max_seconds=max_seconds
        self.started=time.monotonic(); self.count=0; self.fatal=None
        self.context={}; self.originals=[]

    def install(self):
        for name in ('httpx','httpx2'):
            mod=importlib.import_module(name)
            original=mod.AsyncClient.send
            self.originals.append((mod.AsyncClient,original))
            mod.AsyncClient.send=self._wrap(mod,original,name)

    def uninstall(self):
        for cls,original in reversed(self.originals): cls.send=original
        self.originals=[]

    def _wrap(self,mod,original,name):
        recorder=self
        async def send(client,request,**kwargs):
            if request.url.host=='synthetic-test': return await original(client,request,**kwargs)
            if request.url.host!='api.deepseek.com': raise RuntimeError('Unexpected evaluation outbound host')
            if recorder.fatal: raise RuntimeError('Evaluation stopped: '+recorder.fatal)
            if recorder.count>=recorder.max_requests or time.monotonic()-recorder.started>recorder.max_seconds:
                recorder.fatal='provider_budget'; raise RuntimeError('Evaluation provider budget reached')
            recorder.count+=1
            start=time.monotonic()
            row={'index':recorder.count,'transport':name,**recorder.context,'started_at':datetime.now(timezone.utc).isoformat(),'request_raw':request.content.decode('utf-8'),'request':json.loads(request.content),'host':request.url.host,'path':request.url.path,'response':None,'complete':False}
            recorder.sink(row)
            def finish(raw=None,error=None,complete=False):
                row.update(elapsed_ms=round((time.monotonic()-start)*1000,2),finished_at=datetime.now(timezone.utc).isoformat(),complete=complete)
                if raw is not None:
                    row['raw_response']=raw.decode('utf-8',errors='replace')
                    try: row['response']=json.loads(row['raw_response'])
                    except ValueError:
                        row['stream_events']=[]
                        for line in row['raw_response'].splitlines():
                            if line.startswith('data: ') and line[6:]!='[DONE]':
                                try: row['stream_events'].append(json.loads(line[6:]))
                                except ValueError: row['unparsed_stream_event']=True
                if error: row['error_type']=error
                recorder.sink(row)
            try:
                response=await original(client,request,**kwargs)
                row['status']=response.status_code
                if response.status_code in (401,403,429): recorder.fatal='HTTP '+str(response.status_code)
                if response.is_stream_consumed: finish(response.content,complete=True)
                else:
                    upstream=response.stream
                    class Tee(mod.AsyncByteStream):
                        def __init__(self): self.chunks=[]; self.done=False
                        async def __aiter__(self):
                            try:
                                async for chunk in upstream:
                                    self.chunks.append(chunk); yield chunk
                                self.done=True; finish(b''.join(self.chunks),complete=True)
                            except BaseException as exc:
                                finish(b''.join(self.chunks),type(exc).__name__); raise
                        async def aclose(self):
                            await upstream.aclose()
                            if not self.done: finish(b''.join(self.chunks),'stream_not_exhausted')
                    response.stream=Tee()
                return response
            except BaseException as exc:
                finish(error=type(exc).__name__); raise
        return send

