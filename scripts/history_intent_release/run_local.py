"""Run isolated, uncredentialed tests and retain the complete transcript."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.tmp/agent-history-intent-20261108'
OUT=ROOT/'artifacts/agent-dev-notes/history-intent-release-20261108'
parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['before','targeted','before_dates','targeted_dates']);args=parser.parse_args()
tests=['tests/test_history_intent_release.py']
if args.stage=='before_dates':tests=['tests/test_history_revision_dates.py']
if args.stage=='targeted_dates':tests+=['tests/test_history_revision_dates.py']
if args.stage in {'targeted','targeted_dates'}:
    tests+=['tests/test_intent_completion_count.py','tests/test_agent_task_state.py','tests/test_agent_intent_model.py','tests/test_query_history_delivery.py']
command=['docker','run','--rm','--name','fitness-history-intent-'+args.stage,'--network','fitness-experience-20261108','--cpus','2','--memory','2g',
    '-v',f'{SOURCE}:/source:ro','-v',f'{OUT}:/out','-w','/source/backend',
    '-e','DATABASE_URL=postgresql+asyncpg://fitness:local-experience-only@fitness-experience-20261108-pg:5432/history_intent_unit_test',
    '-e','TEST_DATABASE_URL=postgresql+asyncpg://fitness:local-experience-only@fitness-experience-20261108-pg:5432/history_intent_unit_test',
    '-e','SECRET_KEY=isolated-history-intent-test-not-for-production',
    '-e','PYTHONPATH=/source/backend','-e','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python','fitness-agent-backend-ci:0.5.36-local',
    '-m','pytest','-q','--tb=short','-p','no:cacheprovider',*tests,f'--junitxml=/out/{args.stage}-tests.xml']
log=OUT/f'{args.stage}-tests.log'
assert not log.exists(), 'Keep previous attempts; choose an explicit new label before retrying.'
with log.open('w',encoding='utf-8') as stream:
    result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
(OUT/f'{args.stage}-exit.json').write_text(json.dumps({'exit_code':result.returncode,'command':command},indent=2),encoding='utf-8')
print(log.read_text(encoding='utf-8')[-3500:])
raise SystemExit(result.returncode)
