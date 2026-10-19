"""Reconcile tool dispatch and invocation evidence without inventing execution.

Function wrappers cannot observe a ToolNode rejection before BaseTool. Keep that
denominator and reconcile it against persisted dispatch events. Missing,
ambiguous or conflicting evidence makes rates unavailable, never perfect.
This module does not execute tools or grade the business answer.
"""
from collections import Counter


def tool_attempt_ledger(record):
    functions=list(record.get('tool_attempts', []))
    audits=list(record.get('tool_audits', []))
    providers=record.get('provider', [])
    rows=[];issues=[];used_functions=set();used_audits=set();seen=set()
    ids=[f.get('attempt_id') for f in functions]
    if any(v is None for v in ids) or len(ids)!=len(set(ids)):
        issues.append('missing_or_duplicate_function_attempt_id')
    audit_ids=[f.get('id') for f in audits]
    if any(v is None for v in audit_ids) or len(audit_ids)!=len(set(audit_ids)):
        issues.append('missing_or_duplicate_audit_id')
    for ti,turn in enumerate(record.get('turns', [])):
        body=turn.get('body') or {};trace=body.get('execution_trace') or {}
        actions=trace.get('actions', []);observations=trace.get('observations', [])
        for action in actions:
            cid=action.get('call_id');key=(ti,cid);problems=[]
            if not cid or key in seen:problems.append('missing_or_duplicate_call_id')
            seen.add(key)
            tool=action.get('tool_id');args=action.get('arguments')
            observed=[o for o in observations if o.get('call_id')==cid]
            persisted=[(i,v) for i,v in enumerate(audits) if v.get('call_id')==cid and v.get('run_id')==body.get('id')]
            if len(observed)!=1:problems.append('missing_or_duplicate_observation')
            if len(persisted)!=1:problems.append('missing_or_duplicate_audit')
            status={'completed':'success','failed':'error'}.get(action.get('status'))
            if not status:problems.append('dispatch_not_terminal')
            if len(observed)==1:
                obs=observed[0]
                if obs.get('tool_id')!=tool or obs.get('status')!=status or obs.get('action_sequence')!=action.get('sequence'):
                    problems.append('observation_mismatch')
            if len(persisted)==1:
                ai,audit=persisted[0];used_audits.add(ai)
                if audit.get('tool_name')!=tool or audit.get('arguments_data')!=args or audit.get('status')!=action.get('status'):
                    problems.append('audit_mismatch')
                if len(observed)==1 and audit.get('result_data')!=observed[0].get('summary'):
                    problems.append('audit_result_mismatch')
            candidates=[]
            for fi,f in enumerate(functions):
                if fi in used_functions or f.get('turn',0)!=ti or f.get('tool')!=tool:continue
                data=f.get('arguments') or {}
                fcid=data.get('id') if data.get('type')=='tool_call' else (f.get('tool_message') or {}).get('tool_call_id')
                fargs=data.get('args') if data.get('type')=='tool_call' else data
                if fargs==args and (fcid is None or fcid==cid):candidates.append((fi,f))
            origin=None;function_id=None;proof=None
            if len(candidates)==1:
                fi,f=candidates[0];used_functions.add(fi);origin='function';function_id=f.get('attempt_id')
                if f.get('status')!=status or not f.get('lifecycle_complete'):
                    problems.append('function_status_or_lifecycle_mismatch')
            elif len(candidates)>1:
                problems.append('ambiguous_function_match')
            else:
                native=[(pi,pr,call) for pi,pr in enumerate(providers) if pr.get('turn',0)==ti
                        for choice in (pr.get('response') or {}).get('choices',[])
                        for call in (choice.get('message') or {}).get('tool_calls',[]) if call.get('id')==cid]
                if len(native)==1:
                    import json
                    pi,pr,call=native[0];fn=call.get('function') or {};name=fn.get('name')
                    offered=[x.get('function',{}).get('name') for x in (pr.get('request') or {}).get('tools',[])]
                    errors=[m for item in providers if item.get('turn',0)==ti for m in (item.get('request') or {}).get('messages',[])
                            if m.get('role')=='tool' and m.get('tool_call_id')==cid]
                    try:parsed=json.loads(fn.get('arguments',''))
                    except (TypeError,ValueError):parsed=None
                    if (status=='error' and name==str(tool).replace('.','_') and parsed==args and name not in offered
                            and errors and all(isinstance(m.get('content'),str) and name+' is not a valid tool' in m['content'] for m in errors)
                            and len(persisted)==1 and persisted[0][1].get('duration_ms')==0):
                        origin='dispatcher_rejection';proof={'provider_index':pi,'native_call_id':cid,'offered_tools':offered,'error':errors[0]['content']}
                    if origin is None and len(persisted)==1 and len(observed)==1:
                        summary=persisted[0][1].get('result_data') or {}
                        batch=[tc.get('id') for choice in (pr.get('response') or {}).get('choices',[])
                               for tc in (choice.get('message') or {}).get('tool_calls',[])]
                        prior_native={tc.get('id') for previous in providers[:pi] if previous.get('turn',0)==ti
                                      for choice in (previous.get('response') or {}).get('choices',[])
                                      for tc in (choice.get('message') or {}).get('tool_calls',[])}
                        used=sum(a.get('call_id') in prior_native for a in actions if a.get('sequence',0)<action.get('sequence',0)
                                 and a.get('call_id') not in batch)
                        limit=summary.get('limit')
                        # Keep the evaluator independently replayable with
                        # stdlib only; use the trace's documented canonical JSON.
                        import hashlib
                        fingerprint=hashlib.sha256(json.dumps(summary,ensure_ascii=False,sort_keys=True,
                            separators=(',',':'),default=str).encode('utf-8')).hexdigest()
                        if (status=='error' and name==str(tool).replace('.','_') and parsed==args
                                and summary.get('error_code')=='read_tool_budget_exhausted'
                                and summary.get('executed') is False and summary.get('dispatch_stage')=='tool_budget_gate'
                                and summary.get('call_id')==cid and summary.get('batch_call_ids')==batch
                                and type(limit) is int and limit>=0 and type(summary.get('used_before')) is int
                                and summary['used_before']==used and used+len(batch)>limit
                                and persisted[0][1].get('duration_ms')==0
                                and observed[0].get('result_fingerprint')==fingerprint):
                            origin='budget_rejection';proof={'provider_index':pi,'native_call_id':cid,
                                'limit':limit,'used_before':used,'batch_call_ids':batch,'executed':False}
                if origin is None:problems.append('no_function_or_verified_dispatcher_rejection')
            row={'turn':ti,'call_id':cid,'tool':tool,'arguments':args,'status':status or 'unresolved',
                 'origin':origin,'function_attempt_id':function_id,'lifecycle_complete':not problems,
                 'problems':problems,'dispatch_proof':proof}
            rows.append(row);issues.extend(f'{ti}:{cid}:{v}' for v in problems)
        for obs in observations:
            if not any(a.get('call_id')==obs.get('call_id') for a in actions):issues.append(f'{ti}:orphan_observation')
    if len(used_functions)!=len(functions):issues.append('unmatched_function_attempts')
    if len(used_audits)!=len(audits):issues.append('unmatched_persisted_audits')
    # Only a dispatched call needs a ledger row. A bare model proposal may be
    # rejected by a protocol finalizer without dispatch; that remains a model
    # protocol metric. A returned ToolMessage proves dispatch did occur.
    for pr in providers:
        ti=pr.get('turn',0)
        for msg in (pr.get('request') or {}).get('messages',[]):
            if msg.get('role')=='tool' and (ti,msg.get('tool_call_id')) not in seen:
                issues.append(f'{ti}:{msg.get("tool_call_id")}:unmatched_tool_message')
    counts=Counter(r['status'] for r in rows);complete=not issues
    return {'version':1,'complete':complete,'issues':issues,'attempts':len(rows),'successes':counts['success'],
            'errors':counts['error'],'unresolved':counts['unresolved'],'function_invocations':len(functions),
            'dispatcher_rejections':sum(r['origin']=='dispatcher_rejection' for r in rows),
            'budget_rejections':sum(r['origin']=='budget_rejection' for r in rows),
            'success_rate':counts['success']/len(rows) if complete and rows else None,
            'error_rate':counts['error']/len(rows) if complete and rows else None,'rows':rows,
            'scope':'Dispatch/action denominator, reconciled with invocations and persisted audit. Serialized tool-like prose is not execution. A complete ledger does not prove task success.'}
