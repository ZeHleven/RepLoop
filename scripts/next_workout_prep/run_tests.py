"""Uncredentialed Docker regression runner. Logs and replay evidence are append-only."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.tmp/agent-next-workout-prep-20261110'
OUT=ROOT/'artifacts/agent-dev-notes/next-workout-prep-20261110'
parser=argparse.ArgumentParser()
parser.add_argument('--baseline',action='store_true')
parser.add_argument('label'); parser.add_argument('tests',nargs='*')
args=parser.parse_args()
if args.baseline:
    SOURCE=ROOT/'.tmp/agent-release-route-billing-20261108'
assert args.label.replace('-','').replace('_','').isalnum()
target=OUT/args.label; target.mkdir(exist_ok=False)
manifest={str(p.relative_to(SOURCE)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted((SOURCE/'backend').rglob('*')) if p.is_file() and p.suffix in {'.py','.json','.ini','.txt'}
    and '__pycache__' not in p.parts}
(target/'source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
tests=args.tests or ['tests/test_required_read_completion.py']
command=['docker','run','--rm','--name','fitness-prep-test-'+args.label,
    '--network','fitness-prep-20261110','--cpus','2','--memory','2g',
    '-v',f'{SOURCE}:/source:ro','-v',f'{target}:/out','-w','/source/backend',
    '-e','DATABASE_URL=postgresql+asyncpg://fitness:prep-local-only@fitness-prep-20261110-pg:5432/prep_test',
    '-e','TEST_DATABASE_URL=postgresql+asyncpg://fitness:prep-local-only@fitness-prep-20261110-pg:5432/prep_test',
    '-e','SECRET_KEY=isolated-next-workout-prep-test-not-for-production',
    '-e','DEEPSEEK_API_KEY=','-e','EMBEDDING_API_KEY=',
    '-e','PREP_EVIDENCE_DIR=/out','-e','PYTHONPATH=/source/backend','-e','PYTHONDONTWRITEBYTECODE=1',
    '--entrypoint','python','fitness-agent-backend-ci:0.5.36-local',
    '-m','pytest','-q','--tb=short','-p','no:cacheprovider',*tests,'--junitxml=/out/tests.xml']
with (target/'tests.log').open('w',encoding='utf-8') as stream:
    proc=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
(target/'result.json').write_text(json.dumps({'exit_code':proc.returncode,'command':command},indent=2),encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
print((target/'tests.log').read_text(encoding='utf-8')[-4000:])
raise SystemExit(proc.returncode)
