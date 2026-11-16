"""Read only previously verified public health endpoints, without credentials."""
import asyncio
import argparse
import datetime as dt
import json
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
BASE = 'https://fitness-agent-api-299063-11-1407919030.sh.run.tcloudbase.com'
parser = argparse.ArgumentParser()
parser.add_argument('--label', default='before-release')
parser.add_argument('--stage', default='before_current_release_deployment')
args = parser.parse_args()
assert re.fullmatch(r'[a-z0-9-]{1,80}', args.label), 'Use a safe unique evidence label.'

async def main():
    async with httpx.AsyncClient(proxy='http://127.0.0.1:7897', timeout=20, follow_redirects=False) as client:
        async def get(path):
            try:
                result = await client.get(BASE + path)
                try:
                    data = result.json()
                except ValueError:
                    data = {'not_json': True}
                return {'path': path, 'status_code': result.status_code, 'body': data}
            except Exception as exc:
                return {'path': path, 'error_type': type(exc).__name__}
        checks = await asyncio.gather(*(get(path) for path in ['/health', '/ready']))
    data = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'base_url': BASE,
            'url_source': 'artifacts/agent-dev-notes/wechat-only-registration-20261023/check_production_registration.py',
            'stage': args.stage, 'model_calls': 0, 'business_writes': 0,
            'verified_build_commit': None, 'note': 'Endpoints do not expose build SHA and do not prove worker liveness or new deployment.', 'checks': checks}
    file = OUT / ('production-health-' + args.label + '.json')
    assert not file.exists(), 'Keep previous observation; choose a new label for a follow-up.'
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(data, ensure_ascii=True))

asyncio.run(main())
