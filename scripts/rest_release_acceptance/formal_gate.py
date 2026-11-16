"""Run frozen official functions with external attribution, caps and evidence."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parent / 'observer'))
from capture import ProviderCapture, RUN_CONTEXT
from agent_eval import source_manifest as shared_observer_manifest


class GateStopped(BaseException):
    """Escape eval exception-to-failure handlers without starting another case."""


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def append(path, value):
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(value, ensure_ascii=False, default=str) + '\n')


async def main(args):
    assert os.environ['DATABASE_URL'].split('@', 1)[-1] == '127.0.0.1:5432/rest_acceptance_formal_test'
    assert not (args.out / 'result.json').exists()
    sys.path.insert(0, str(args.source / 'backend'))
    from sqlalchemy import func, select, text
    from app.database import AsyncSessionLocal, engine
    from app.config import settings
    from app.services.business_clock import calendar_date
    from app.models.agent import AgentRun, AgentToolCall, AgentMessage, AgentArtifact, AgentProposal
    from app.models.meal import MealLog
    from app.models.model_budget import ModelBudget, ModelCharge
    from app.services.model_budget import PRICING_VERSION
    from scripts import evaluate_agent_routing_real as route
    from scripts import evaluate_daily_meal_generation_real as meal

    assert len(route.CASES) == 33 and len(meal.SYNONYMS) == 21
    assert settings.DEEPSEEK_API_KEY
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count(ModelCharge.id))) == 0
        assert await db.scalar(select(func.count(AgentRun.id))) == 0
        assert await db.scalar(select(func.count(AgentProposal.id))) == 0
        assert await db.scalar(select(func.count(MealLog.id))) == 0
        pg_version = await db.scalar(text('select version()'))
    source_files = {str(p.relative_to(args.source)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (args.source / 'backend').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    day = calendar_date(datetime.now(timezone.utc))
    assert str(day) == os.environ['ACCEPTANCE_BUSINESS_DATE'], 'business_day_changed'
    observer = ProviderCapture(args.out, mode='live', max_requests=args.max_requests, max_cny=args.max_cny, max_seconds=3600)
    observer.install()
    reports = {}
    current_stage = 'route'
    route_index = 0
    meal_indexes = {}
    stopped = None

    def guard():
        if observer.fatal:
            raise GateStopped(observer.fatal)
        if calendar_date(datetime.now(timezone.utc)) != day:
            observer.fatal = 'business_day_changed'
            raise GateStopped(observer.fatal)

    def save():
        dump(args.out / 'result.json', {'source_commit': args.commit, 'business_date': day,
             'environment': 'local_frozen_formal_scripts_with_external_observer',
             'github_workflow_executed': False, 'provider_calls': observer.calls,
             'stopped': stopped or observer.fatal, 'reports': reports,
             'all_original_formal_checks_passed': bool(reports.get('route', {}).get('passed') and
                                                       reports.get('meal', {}).get('passed') and not (stopped or observer.fatal))})

    original_resolve = route.resolve_intent_with_fallback

    async def observed_resolve(prompt, **kwargs):
        nonlocal route_index
        guard()
        case = route.CASES[route_index]
        assert prompt == case.prompt
        route_index += 1
        token = RUN_CONTEXT.set({'task_id': 'route:' + case.case_id, 'run_id': None, 'worker_attempt': 1})
        started = time.monotonic()
        try:
            outcome = await original_resolve(prompt, **kwargs)
            append(args.out / 'route-cases.jsonl', {'case': case.case_id, 'prompt': prompt,
                'result': route._case_result(case, outcome), 'resolution': outcome.resolution.model_dump(mode='json'),
                'elapsed_ms': (time.monotonic() - started) * 1000})
            print('PROGRESS route', route_index, '/33', 'http', len(observer.calls), flush=True)
            guard()
            return outcome
        finally:
            RUN_CONTEXT.reset(token)
            save()

    route.resolve_intent_with_fallback = observed_resolve
    original_chat = meal.run_agent_chat

    async def observed_chat(db, **kwargs):
        guard()
        index = meal_indexes.get(current_stage, 0) + 1
        meal_indexes[current_stage] = index
        identity = f'{current_stage}:{index}'
        token = RUN_CONTEXT.set({'task_id': identity, 'run_id': None, 'worker_attempt': 1})
        started = time.monotonic()
        try:
            result = await original_chat(db, **kwargs)
            run = await db.get(AgentRun, result.run_id)
            calls = (await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == result.run_id))).all()
            messages = (await db.scalars(select(AgentMessage).where(AgentMessage.run_id == result.run_id))).all()
            artifacts = (await db.scalars(select(AgentArtifact).where(AgentArtifact.source_run_id == result.run_id))).all()
            def fields(item):
                return {c.name: getattr(item, c.name) for c in item.__table__.columns}
            append(args.out / 'meal-cases.jsonl', {'task_id': identity, 'prompt': kwargs['user_message'],
                'run_id': result.run_id, 'reply': result.reply, 'run': fields(run),
                'tool_calls': [fields(c) for c in calls], 'messages': [fields(m) for m in messages],
                'artifacts': [fields(a) for a in artifacts], 'elapsed_ms': (time.monotonic() - started) * 1000})
            print('PROGRESS', current_stage, index, '/31', 'http', len(observer.calls),
                  'known_cny', round(observer.spent_nano / 1e9, 6), flush=True)
            guard()
            return result
        finally:
            RUN_CONTEXT.reset(token)
            save()

    meal.run_agent_chat = observed_chat
    original_evaluate = meal.evaluate

    async def observed_pass(*, pass_index=1):
        nonlocal current_stage
        guard()
        current_stage = f'meal-p{pass_index}'
        result = await original_evaluate(pass_index=pass_index)
        dump(args.out / f'meal-pass-{pass_index}.json', meal._decorate_report(result, candidate_sha=args.commit))
        return result

    meal.evaluate = observed_pass
    dump(args.out / 'freeze.json', {'candidate': args.commit, 'business_date': day, 'postgres_version': pg_version,
         'source_files': source_files, 'shared_observer_files': shared_observer_manifest(),
         'pricing_version': PRICING_VERSION,
         'settings': {k: v for k, v in settings.model_dump().items() if k.startswith('AGENT_')},
         'max_http': args.max_requests, 'max_estimated_nano': round(args.max_cny * 1e9),
         'scope': 'same official functions and scoring; not a GitHub protected workflow run'})
    try:
        reports['route'] = await route.evaluate()
        reports['route']['candidate_sha'] = args.commit
        dump(args.out / 'agent-route-live-eval.json', reports['route'])
        save()
        guard()
        reports['meal'] = meal._decorate_report(await meal.evaluate_repeated(2), candidate_sha=args.commit)
        dump(args.out / 'daily-meal-live-eval.json', reports['meal'])
    except GateStopped:
        stopped = observer.fatal or 'controlled_stop'
    except Exception as exc:
        stopped = 'harness_or_evaluation_error:' + type(exc).__name__
        import traceback
        (args.out / 'exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        save()
        async with AsyncSessionLocal() as db:
            charges = (await db.scalars(select(ModelCharge))).all()
            budgets = (await db.scalars(select(ModelBudget))).all()
            def fields(item):
                return {c.name: getattr(item, c.name) for c in item.__table__.columns}
            dump(args.out / 'ledger.json', {'charges': [fields(c) for c in charges], 'budgets': [fields(b) for b in budgets],
                 'pending_runs': await db.scalar(select(func.count(AgentRun.id)).where(AgentRun.status.in_(('queued','running','processing')))),
                 'meal_rows': await db.scalar(select(func.count(MealLog.id))),
                 'proposal_rows': await db.scalar(select(func.count(AgentProposal.id)))})
        assert all(hashlib.sha256((args.source / p).read_bytes()).hexdigest() == h for p, h in source_files.items())
        dump(args.out / 'source-verification.json', {'files': len(source_files), 'before_after_equal': True})
        await engine.dispose()
    print(json.dumps({'formal_completed': 'meal' in reports, 'http': len(observer.calls),
          'known_cny': observer.spent_nano / 1e9, 'unknown_occupancy_cny': observer.reserved_nano / 1e9,
          'stopped': stopped or observer.fatal}), flush=True)
    passed = reports.get('route', {}).get('passed') and reports.get('meal', {}).get('passed')
    return 0 if passed and not stopped and not observer.fatal else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--max-requests', type=int, required=True)
    parser.add_argument('--max-cny', type=float, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
