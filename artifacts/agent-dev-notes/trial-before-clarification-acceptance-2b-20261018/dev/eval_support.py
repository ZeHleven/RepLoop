"""Eval-only durable budgets and read-only authentication recovery."""
import asyncio,json,re,time
from pathlib import Path
from uuid import uuid4

from durable_budget import atomic_json, Budget, EvaluationStopped
from clock_policy import finite

READ_RESULT=re.compile(r'/api/v1/agent/(?:runs/[^/]+(?:/attempts)?|conversations/[^/]+/messages)')
def elapsed_validity(start_wall,start_mono,wall=time.time,mono=time.monotonic,gap=.05):
 w,m=wall(),mono();wall_ms=(w-start_wall)*1000;mono_ms=(m-start_mono)*1000
 valid=all(finite(v) for v in [w,m,start_wall,start_mono]) and m>=start_mono and abs(wall_ms-mono_ms)<=gap*1000
 clean=lambda v:round(v,2) if finite(v) else None
 return dict(clock_valid=valid,elapsed_ms=clean(mono_ms),elapsed_wall_ms=clean(wall_ms),clock_deviation_ms=clean(wall_ms-mono_ms),clock_quality_tolerance_ms=gap*1000)

async def recorded_request(client,method,path,payload,headers,refresh,rows,save,*,budget=None,wall=time.time,mono=time.monotonic,expected_outcome=None):
 for attempt in range(2):
  if budget:budget.check()
  started_wall=wall();start=mono()
  row=dict(attempt_id=uuid4().hex,method=method,path=path,request=payload,attempt=attempt,status=None,lifecycle_complete=False,response_complete=False,started_wall=started_wall,started_mono=start)
  if expected_outcome is not None:row['expected_outcome']=dict(expected_outcome)
  rows.append(row);save()
  try:
   response=await client.request(method,path,headers=headers,json=payload)
   row.update(status=response.status_code,response_complete=True)
   try:row['body']=response.json()
   except (ValueError,TypeError):
    row['raw_response']=response.text;raise
  except BaseException as exc:
   row.update(error_type=type(exc).__name__);raise
  finally:
   row.update(elapsed_validity(started_wall,start,wall,mono))
   row.update(lifecycle_complete=True,finished_wall=started_wall+row['elapsed_wall_ms']/1000 if row['elapsed_wall_ms'] is not None else None)
   if budget:
    try:budget.check()
    except EvaluationStopped:
     row['stop_reason']=budget.state['stop']
     if 'clock' in row['stop_reason'] or 'monotonic' in row['stop_reason']:row['clock_valid']=False
   save()
  # Preserve even a successful terminal response if its timing is invalid.
  # The caller persists its Turn and then checks the sticky budget stop.
  if budget and budget.state['stop']:return response
  if response.status_code==401 and attempt==0 and method=='GET' and READ_RESULT.fullmatch(path):
   headers.update(Authorization='Bearer '+refresh());row['refresh_for_read_only_retry']=True;save();continue
  return response

async def guarded_wait(awaitable,budget,timeout):
 task=asyncio.ensure_future(awaitable);started=time.monotonic()
 try:
  while not task.done():
   await asyncio.wait({task},timeout=min(1,timeout))
   budget.check()
   if time.monotonic()-started>timeout:raise TimeoutError('Run timeout')
  return await task
 finally:
  if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
