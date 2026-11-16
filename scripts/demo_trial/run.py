"""Observe a frozen demo through its real UI; credentials stay in process memory.

Run from the repository root. `prepare` migrates/seeds the dedicated demo DB;
`serve` enables the existing model config with a durable 5-yuan global cap;
`snapshot` only exports synthetic SQL evidence. Create OUT/STOP for graceful exit.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name('cases.json')
DATABASE = 'hackathon_live_20261115_demo'
DATABASE_URL = f'postgresql+asyncpg://demo:demo_local_only@127.0.0.1:5547/{DATABASE}'
MODEL_KEYS = ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_CHAT_MODEL',
              'DEEPSEEK_REASONING_MODEL', 'AGENT_MODEL', 'AGENT_INTENT_MODEL')
PRIVATE_FIELDS = {'access_token', 'refresh_token', 'request_key', 'password',
                  'password_hash', 'key_hash', 'client_hash', 'authorization'}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def sanitized(value):
    if isinstance(value, dict):
        return {k: sanitized(v) for k, v in value.items() if k.lower() not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    return value


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, encoding='utf-8').strip()


def source_fingerprints():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names if name}


def configure(live):
    from dotenv import dotenv_values
    model = dotenv_values(ROOT / '.env')
    local = dotenv_values(ROOT / '.env.demo.local')
    for key in MODEL_KEYS:
        if model.get(key):
            os.environ[key] = model[key]
    if not live:
        os.environ['DEEPSEEK_API_KEY'] = ''
    if live and not os.environ.get('DEEPSEEK_API_KEY'):
        raise SystemExit('Existing model configuration has no API key')
    endpoint = urlsplit(os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
    if endpoint.scheme != 'https' or endpoint.hostname != 'api.deepseek.com' or endpoint.username or endpoint.password or endpoint.query:
        raise SystemExit('The configured endpoint is outside the verified budget transport')
    if not local.get('SECRET_KEY'):
        raise SystemExit('Create the local demo configuration with scripts/run_demo.py first')
    os.environ.update(DATABASE_URL=DATABASE_URL, SECRET_KEY=local['SECRET_KEY'],
        DEMO_ENABLED='true', DEMO_STATIC_DIR=str(ROOT / 'miniapp/dist-h5'),
        DEMO_USER_BUDGET_NANO='5000000000', DEMO_TOTAL_BUDGET_NANO='5000000000',
        AGENT_ASYNC_WORKER_ENABLED='true', RAG_ENABLED='false', EMBEDDING_API_KEY='',
        PYTHONUNBUFFERED='1', PYTHONUTF8='1')
    # Do not load the normal app database or unrelated .env settings.
    os.chdir(ROOT / 'backend')
    sys.path.insert(0, str(ROOT / 'backend'))
    sys.path.insert(0, str(ROOT / 'scripts'))


async def snapshot():
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app import models  # noqa: F401
    from app.models.profile import UserProfile, WeightLog
    from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
    from app.models.meal import MealLog, MealItem
    from app.models.agent import AgentConversation, AgentRun, AgentMessage, AgentToolCall, AgentProposal
    from app.models.demo import DemoVisit, DemoIdentity
    from app.models.model_budget import ModelBudget, ModelCharge
    business = (UserProfile, WeightLog, WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise, MealLog, MealItem, AgentProposal)
    records = business + (AgentConversation, AgentRun, AgentMessage, AgentToolCall, DemoVisit, DemoIdentity, ModelBudget, ModelCharge)
    result = {}
    async with AsyncSessionLocal() as db:
        for model in records:
            rows = (await db.scalars(select(model))).all()
            values = [sanitized({c.name: getattr(row, c.name) for c in model.__table__.columns}) for row in rows]
            result[model.__tablename__] = sorted(values, key=lambda r: json.dumps(r, sort_keys=True, default=str))
    result = json.loads(json.dumps(result, default=str))
    result['business_sha256'] = hashlib.sha256(json.dumps(
        {m.__tablename__: result[m.__tablename__] for m in business}, sort_keys=True).encode()).hexdigest()
    return result


async def serve(args):
    import uvicorn
    from sqlalchemy import select
    from agent_eval.capture import ProviderCapture, RUN_CONTEXT
    from app.config import settings
    from app.database import AsyncSessionLocal, engine
    from app.main import app
    from app.models.agent import AgentRun, AgentMessage
    from app.services import agent_jobs
    from app.services.model_budget import PRICING_VERSION, RATES
    from package_demo import validate_h5_build

    if git('status', '--porcelain') or git('rev-parse', 'HEAD') != args.commit:
        raise SystemExit('The demo trial requires the specified clean candidate')
    identity = validate_h5_build()
    write(ROOT / 'backend/app/build_metadata.json', identity)
    contract = json.loads(CASES.read_text(encoding='utf-8'))
    before = await snapshot()
    if before['agent_runs'] or before['model_charges'] or before['demo_visits']:
        raise SystemExit('This one-shot trial requires its previously prepared fresh database')
    freeze = source_fingerprints()
    with (args.out / 'serve-once.json').open('x', encoding='utf-8') as handle:
        json.dump({'commit': args.commit, 'started_at': datetime.now(timezone.utc).isoformat()}, handle)
    write(args.out / 'freeze.json', {'commit': args.commit, 'build': identity,
        'source_files': freeze, 'cases': contract, 'database': DATABASE,
        'model_config': {key: getattr(settings, key) for key in MODEL_KEYS if key != 'DEEPSEEK_API_KEY'},
        'agent_settings': {k: v for k, v in settings.model_dump().items() if k.startswith('AGENT_')},
        'pricing_version': PRICING_VERSION, 'rates_nano_per_token': RATES,
        'scope': 'Real browser on localhost, real API/worker/model, synthetic demo data; no public deployment or physical phone performance claim.'})
    write(args.out / 'before.json', before)
    observer = ProviderCapture(args.out, mode='live', max_cny=5,
        max_requests=contract['max_provider_requests'], max_seconds=1200)
    observer.install()
    original_process = agent_jobs.process_agent_run
    registered = {}

    async def observed_process(factory, run_id):
        async with factory() as db:
            run = await db.get(AgentRun, run_id)
            message = await db.scalar(select(AgentMessage.content).where(
                AgentMessage.run_id == run_id, AgentMessage.role == 'user'))
        if run_id not in registered:
            index = len(registered)
            if index >= len(contract['cases']) or message != contract['cases'][index]['message']:
                observer.fatal = 'unregistered_trial_turn'
                raise RuntimeError(observer.fatal)
            registered[run_id] = contract['cases'][index]['id']
        case = registered[run_id]
        attempt = f'{case}-attempt{run.attempt_count}'
        write(args.out / f'{attempt}-before.json', await snapshot())
        token = RUN_CONTEXT.set({'task_id': case, 'run_id': run_id,
                                 'user_id': run.user_id, 'worker_attempt': run.attempt_count})
        try:
            return await original_process(factory, run_id)
        finally:
            RUN_CONTEXT.reset(token)
            write(args.out / f'{attempt}-after.json', await snapshot())
            write(args.out / 'capture-progress.json', {'registered': registered, 'calls': len(observer.calls),
                'spent_nano': observer.spent_nano, 'reserved_nano': observer.reserved_nano, 'fatal': observer.fatal})

    agent_jobs.process_agent_run = observed_process

    class ApiCapture:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http' or not scope['path'].startswith('/api/v1/'):
                return await self.app(scope, receive, send)
            request_parts, response_parts = [], []
            event = {'path': scope['path'], 'method': scope['method'], 'started_wall': time.time()}
            start = time.monotonic()

            async def incoming():
                message = await receive()
                if message['type'] == 'http.request':
                    request_parts.append(message.get('body', b''))
                return message

            async def outgoing(message):
                if message['type'] == 'http.response.start':
                    event['status'] = message['status']
                elif message['type'] == 'http.response.body':
                    response_parts.append(message.get('body', b''))
                await send(message)

            try:
                return await self.app(scope, incoming, outgoing)
            finally:
                event['elapsed_ms'] = (time.monotonic() - start) * 1000
                for key, parts in [('request', request_parts), ('response', response_parts)]:
                    raw = b''.join(parts)
                    if raw:
                        try:
                            event[key] = sanitized(json.loads(raw))
                        except ValueError:
                            event[key] = {'non_json_bytes': len(raw)}
                with (args.out / 'api-events.jsonl').open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + '\n')

    app.add_middleware(ApiCapture)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=False, log_level='info'))

    async def watch_stop():
        while not server.should_exit:
            if (args.out / 'STOP').exists() or time.monotonic() - observer.started > 1200:
                server.should_exit = True
                break
            await asyncio.sleep(.5)

    watcher = asyncio.create_task(watch_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        write(args.out / 'after.json', await snapshot())
        write(args.out / 'provider-calls.json', observer.calls)
        write(args.out / 'result.json', {'commit': args.commit, 'registered': registered,
            'provider_calls': len(observer.calls), 'spent_nano': observer.spent_nano,
            'reserved_nano': observer.reserved_nano, 'fatal': observer.fatal,
            'source_unchanged': source_fingerprints() == freeze})
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'serve', 'snapshot'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--commit', default='')
    parser.add_argument('--port', type=int, default=8019)
    parser.add_argument('--label', default='manual')
    args = parser.parse_args()
    args.out = args.out.resolve()
    if not args.out.is_relative_to(ROOT / 'artifacts') or not args.out.is_dir():
        parser.error('Use an existing evidence directory under root/artifacts')
    configure(args.stage == 'serve')
    if args.stage == 'prepare':
        for command in ([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                        [sys.executable, 'scripts/seed_exercises.py'],
                        [sys.executable, 'scripts/seed_foods.py']):
            subprocess.run(command, check=True)
        write(args.out / 'fixture-before.json', asyncio.run(snapshot()))
    elif args.stage == 'snapshot':
        if not args.label.replace('-', '').isalnum():
            parser.error('Snapshot label must be alphanumeric')
        write(args.out / f'{args.label}.json', asyncio.run(snapshot()))
    else:
        if not args.commit:
            parser.error('serve requires --commit')
        asyncio.run(serve(args))


if __name__ == '__main__':
    main()
