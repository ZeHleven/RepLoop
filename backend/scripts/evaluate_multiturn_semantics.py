"""Semantic-only multi-turn evaluation; synthetic acknowledgements are fixtures."""
import argparse, asyncio, hashlib, json, sys, time
from pathlib import Path
BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from app.services import agent_intent_model as im
from evals.multiturn_contracts import score_semantic
from evaluate_agent_tasks import source_fingerprint
CASES=json.loads((BACKEND/'evals/multiturn_cases.json').read_text(encoding='utf-8'))['cases']

async def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output', required=True); parser.add_argument('--split', default='development'); parser.add_argument('--without-state', action='store_true'); parser.add_argument('--replay-from', type=Path)
    args=parser.parse_args(); args.state=not args.without_state; out=Path(args.output); out.mkdir(exist_ok=False)
    results=[]
    for case in CASES:
        if args.split != 'all' and case['split'] != args.split: continue
        if args.replay_from:
            path=args.replay_from/(case['id']+'.json')
            record=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            score=score_semantic(case,record.get('turns',[]))
            results.append({'id':case['id'],**score})
            (out/(case['id']+'.json')).write_text(json.dumps({'case':case,'replay_from':str(path),**score},ensure_ascii=False,indent=2),encoding='utf-8')
            continue
        history=[]; snapshot=None; rows=[]; start=time.perf_counter()
        for index, message in enumerate(case['turns']):
            if index and case['gap']:
                history.extend([{'role':'assistant', 'content':'这是合成的长答复占位。'*70}, {'role':'user','content':'嗯'}, {'role':'assistant','content':'收到'}, {'role':'user','content':'好的'}, {'role':'assistant','content':'明白'}])
            context=list(history)
            if args.state and snapshot:
                context.insert(0, {'role':'task_state', 'content':json.dumps(snapshot, ensure_ascii=False)})
            raw=[]; original=im.structured_chat_completion
            async def capture(*a, **kw):
                value=await original(*a, **kw); raw.append({'messages':a[0], 'payload':value.payload, 'raw':value.raw_output, 'mode':value.mode}); return value
            im.structured_chat_completion=capture
            try:
                outcome=await im.resolve_intent_with_fallback(message, context_messages=context, use_model=True)
                resolution=outcome.resolution
                if args.state:
                    from app.services.agent_task_state import advance_task_state
                    snapshot=advance_task_state(snapshot, resolution.task_update, message=message, run_id=f'{case["id"]}-{index}', normalized_request=resolution.resolved_query).model_dump(mode='json')
                rows.append({'message':message,'resolution':resolution.model_dump(mode='json'),'source':outcome.source,'understanding_failed':outcome.understanding_failed,'raw':raw,'state':snapshot,
                             'latency_ms':outcome.latency_ms,'attempt_count':outcome.attempt_count})
                history.extend([{'role':'user','content':message},{'role':'assistant','content':'已收到。此句为评测夹具，不代表真实业务完成。'}])
            except Exception as exc:
                rows.append({'error':type(exc).__name__+':'+str(exc), 'raw':raw}); break
            finally: im.structured_chat_completion=original
        last=rows[-1]; query=last.get('resolution',{}).get('resolved_query','')
        # Judge only normalized execution request. Stored state presence alone is not success.
        score=score_semantic(case, rows); checks=score['checks']; passed=score['passed']
        result={'case':case,'data_source':'synthetic','scope':'semantic route only; no API or UI completion claim','turns':rows,'checks':checks,'passed':passed,'duration_ms':round((time.perf_counter()-start)*1000)}
        (out/(case['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append({'id':case['id'],'passed':passed,'checks':checks}); print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    (out/'report.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'metadata.json').write_text(json.dumps({'source':source_fingerprint(),
        'source_role':'evaluator_replay' if args.replay_from else 'execution_and_evaluator',
        'dataset_sha256':hashlib.sha256((BACKEND/'evals/multiturn_cases.json').read_bytes()).hexdigest(),
        'scope':'semantic routing only; intermediate assistant acknowledgements and gaps are synthetic fixtures; no user task completion claim',
        'split':args.split,'state_enabled':args.state},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'failure-inbox.json').write_text(json.dumps([dict(row,classification='untriaged') for row in results if not row['passed']],ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(row['passed'] for row in results) else 1

if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
