"""One paid final-answer replay; saved prior messages + real read, not a full task run."""
from pathlib import Path
import asyncio
from datetime import date
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, '/observer')
sys.path.insert(0, '/source/backend')
from capture import ProviderCapture, RUN_CONTEXT
from agent_eval import source_manifest as shared_observer_manifest

OUT = Path('/out')
SOURCE = Path('/source')
ORIGINAL = Path('/original')


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def manifest():
    return {p.relative_to(SOURCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((SOURCE / 'backend').rglob('*')) if p.is_file()
            and p.suffix in {'.py', '.json', '.txt', '.ini', '.sh'} and '__pycache__' not in p.parts}


async def main():
    from sqlalchemy import select, text, func
    from langchain_core.messages import convert_to_messages
    from app.database import AsyncSessionLocal, engine
    from app.models.agent import AgentRun, AgentToolCall, AgentMessage, AgentProposal
    from app.models.model_budget import ModelCharge
    from app.models.profile import UserProfile
    from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession
    from app.models.meal import MealLog, MealItem
    from app.services import agent_runtime as runtime
    from app.services.business_clock import BUSINESS_DATE
    from app.services.agent_intent import IntentResolution
    from app.services.agent_evidence_contract import build_evidence_contract, guard_read_completion
    from app.services.agent_trace import build_initial_execution_trace, complete_execution_trace
    from app.services.model_budget import billing_user

    assert os.environ['DATABASE_URL'].endswith('@fitness-prep-live-20261110-pg:5432/prep_live_test')
    assert not (OUT / 'result.json').exists()
    registration = json.loads((OUT / 'preregistration.json').read_text(encoding='utf-8'))
    assert registration['max_http_requests'] == 1
    assert registration['prior_calls'] == 9 and registration['max_estimated_cny'] == 0.337336
    prior = json.loads((ORIGINAL / 'result.json').read_text(encoding='utf-8'))
    task = next(t for t in prior['tasks'] if t['case_id'] == 'P02')
    body = task['turns'][0]['response']
    failed = next(c for c in prior['provider_calls'] if c['id'] == 'http-9')
    assert failed['status_code'] == 400
    raw_messages = failed['request']['messages']
    cut = next(i for i, m in enumerate(raw_messages) if any(
        c['id'].startswith('required-') for c in m.get('tool_calls', [])))
    # The helper supplies the base system prompt itself. Keep the actual task
    # context, original model outputs and original next-workout observation.
    original_result = {'messages': convert_to_messages(raw_messages[1:cut])}
    intent_fields = {k: v for k, v in body.items() if k in IntentResolution.model_fields}
    intent_fields['confidence'] = body['intent_confidence']
    resolution = IntentResolution.model_validate(intent_fields)
    before_source = manifest()
    write('freeze.json', {'source_commit': registration['candidate'], 'source_files': before_source,
                         'shared_observer_files': shared_observer_manifest(),
                         'original_http': 'live-01/http-9', 'business_date': prior['business_date'],
                         'scope': 'saved-model-messages plus actual plan SQL read and one real final-answer request; no API/worker rerun'})

    async def snapshot(db):
        data = {}
        for model in [WorkoutPlan, PlannedExercise, WorkoutSession, AgentProposal, UserProfile, MealLog, MealItem]:
            table = model.__tablename__
            data[table] = (await db.scalars(text(f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY id'))).all()
        for model in [AgentRun, AgentToolCall, AgentMessage]:
            data[model.__tablename__ + '_count'] = await db.scalar(select(func.count()).select_from(model))
        return data

    observer = ProviderCapture(OUT, mode='live', max_requests=1, max_cny=0.337336, max_seconds=180)
    observer.install()
    token = RUN_CONTEXT.set({'task_id': 'P02-answer-replay', 'run_id': None, 'worker_attempt': 0})
    day_token = BUSINESS_DATE.set(date.fromisoformat(prior['business_date']))
    try:
        async with AsyncSessionLocal() as db:
            assert await db.scalar(text('SELECT current_database()')) == 'prep_live_test'
            before = await snapshot(db)
            old_ids = set((await db.scalars(select(ModelCharge.id))).all())
            assert len(old_ids) == 9, 'Do not repeat the paid answer replay.'
            tools = runtime.build_read_tools(db, user_id=task['user_id'], allowlist=body['tool_allowlist'])
            model = runtime._build_model()
            started = time.monotonic()
            try:
                with billing_user(task['user_id']):
                    result = await runtime._complete_direct_required_reads(original_result, db=db,
                        model=model, tools=tools, resolution=resolution, tool_allowlist=body['tool_allowlist'])
            finally:
                await model.http_async_client.aclose()
            elapsed_ms = round((time.monotonic() - started) * 1000, 3)
            trace = build_initial_execution_trace(resolution, body['tool_allowlist']).model_copy(update={
                'evidence_contract': build_evidence_contract(resolution, body['tool_allowlist'],
                    budget=runtime.settings.AGENT_MAX_TOOL_CALLS)})
            trace = complete_execution_trace(trace, result, summarize_observation=runtime._audit_result_summary)
            reply, trace = guard_read_completion(result['messages'][-1].content, trace)
            after = await snapshot(db)
            charges = (await db.scalars(select(ModelCharge).where(ModelCharge.id.not_in(old_ids)))).all()
            pending = await db.scalar(select(func.count(AgentRun.id)).where(AgentRun.status.in_(('queued', 'running'))))
            write('result.json', {'source_commit': registration['candidate'], 'scope': 'targeted_real_answer_replay_not_full_agent_task',
                'reply': reply, 'trace': trace.model_dump(mode='json'), 'elapsed_ms': elapsed_ms,
                'messages': [m.model_dump(mode='json') for m in result['messages']],
                'before': before, 'after': after, 'business_unchanged': before == after,
                'new_charges': [{'id': c.id, 'status': c.status, 'reserved_nano': c.reserved_nano,
                                'actual_nano': c.actual_nano} for c in charges],
                'pending_runs': pending, 'provider_calls': observer.calls, 'stopped': observer.fatal,
                'known_estimated_cny': observer.spent_nano / 1e9, 'unknown_reserved_cny': observer.reserved_nano / 1e9})
            assert before == after and pending == 0
            assert len(observer.calls) == len(charges) == 1
            assert observer.calls[0]['request']['thinking'] == {'type': 'disabled'}
            assert 'tools' not in observer.calls[0]['request']
            assert observer.calls[0]['status_code'] == 200 and observer.calls[0]['usage_complete']
            assert trace.evidence_contract.status == 'complete'
            assert trace.termination_reason != 'required_read_answer_unavailable'
            print(json.dumps({'calls': len(observer.calls), 'known_estimated_cny': observer.spent_nano / 1e9,
                              'elapsed_ms': elapsed_ms, 'business_unchanged': before == after}), flush=True)
    finally:
        write('source-verification.json', {'before_after_equal': before_source == manifest(), 'files': len(before_source)})
        BUSINESS_DATE.reset(day_token)
        RUN_CONTEXT.reset(token)
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
