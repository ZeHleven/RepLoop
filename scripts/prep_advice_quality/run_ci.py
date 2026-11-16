"""Launch the CI-equivalent checks and retain all output, without live credentials."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.tmp/agent-prep-advice-20261110'
parser=argparse.ArgumentParser(); parser.add_argument('label',default='ci',nargs='?'); args=parser.parse_args()
assert args.label.replace('-','').isalnum()
OUT=ROOT/'artifacts/agent-dev-notes/preparation-advice-quality-20261110'/args.label
OUT.mkdir(exist_ok=False)
manifest={p.relative_to(SOURCE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted((SOURCE/'backend').rglob('*')) if p.is_file() and p.suffix in {'.py','.json','.ini','.txt'}
    and '__pycache__' not in p.parts}
(OUT/'source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
command=['docker','run','--rm','--name','fitness-advice-'+args.label,'--network','container:fitness-advice-20261110-pg',
    '--cpus','2','--memory','2g','-v',f'{SOURCE}:/source:ro','-v',f'{OUT}:/out',
    '-v',f'{ROOT / "scripts/prep_advice_quality"}:/harness:ro',
    '-e',f'DATABASE_URL=postgresql+asyncpg://fitness:advice-local-only@127.0.0.1:5432/advice_{args.label}_test',
    '-e',f'TEST_DATABASE_URL=postgresql+asyncpg://fitness:advice-local-only@127.0.0.1:5432/advice_{args.label}_test',
    '-e',f'AGENT_QUERY_EVAL_DATABASE_URL=postgresql+asyncpg://fitness:advice-local-only@127.0.0.1:5432/advice_{args.label}_test',
    '-e','SECRET_KEY=isolated-next-workout-prep-test-not-for-production','-e','DEEPSEEK_API_KEY=',
    '-e','EMBEDDING_API_KEY=','-e','PREP_EVIDENCE_DIR=/out','-e','PYTHONPATH=/source/backend',
    '-e','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python','fitness-agent-backend-ci:0.5.36-local','/harness/checks.py']
with (OUT/'runner.log').open('w',encoding='utf-8') as log:
    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
(OUT/'result.json').write_text(json.dumps({'command':command,'exit_code':result.returncode},indent=2),encoding='utf-8')
print((OUT/'runner.log').read_text(encoding='utf-8')[-5000:])
raise SystemExit(result.returncode)
