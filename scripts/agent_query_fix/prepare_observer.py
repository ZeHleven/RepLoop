"""Reuse the frozen collector; keep new validation artifacts separate."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / 'observer'
OUT.mkdir(exist_ok=True)
FROZEN = ROOT / 'artifacts/agent-dev-notes/current-experience-metrics-20261108/frozen-observer'
for name in ('capture.py', 'metrics.py'):
    shutil.copyfile(ROOT / 'scripts/agent_experience' / name, OUT / name)
runner = (FROZEN / 'run_baseline.py').read_text(encoding='utf-8')
changes = {
    'from metrics import clock_interval, summarize\n': 'from metrics import clock_interval, summarize\nfrom agent_eval import source_manifest as shared_observer_manifest\n',
    '              "runner_sha256":': '              "shared_observer_files": shared_observer_manifest(),\n              "runner_sha256":',
    '(Path(__file__).parent / "tasks.json").read_text(encoding="utf-8")': 'args.tasks.read_text(encoding="utf-8")',
    '(Path(__file__).parent / "tasks.json").read_bytes()': 'args.tasks.read_bytes()',
    '"limits": {"tasks": 16, "runs": 48,': '"limits": {"tasks": len(contract["cases"]) * len(contract["cohorts"]), "runs": 48,',
    'p.add_argument("--source", type=Path, required=True)': 'p.add_argument("--source", type=Path, required=True)\n    p.add_argument("--tasks", type=Path, required=True)',
}
for before, after in changes.items():
    assert before in runner, before
    runner = runner.replace(before, after)
(OUT / 'run_baseline.py').write_text(runner, encoding='utf-8')
shutil.copyfile(FROZEN / 'tasks.json', OUT / 'tasks-core.json')
core = json.loads((FROZEN / 'tasks.json').read_text(encoding='utf-8'))
extra = {**core, 'authorship': 'Implementation agent; frozen before candidate live outputs. Additional wording checks, not blind or independent validation.', 'cases': [
    {'id': 'X01', 'category': 'history_relative_range', 'turns': [
        '请列过去一周完成过的训练，每场日期和用了多少分钟。',
        '范围收窄到最近两天，仍只要已完成的日期和用时。'],
     'acceptance': '第一轮前天30分钟和昨天12分钟；第二轮仅昨天12分钟，不再包含前天或今天进行中场次。保持只读，无业务写入。'},
    {'id': 'X02', 'category': 'history_relative_day', 'turns': [
        '列出最近7天的已完成训练：日期、每场时长。',
        '查询区间更正为昨天，继续只显示已完成场次的日期及每场用时。'],
     'acceptance': '第一轮前天30分钟和昨天12分钟；第二轮仅昨天12分钟，范围确实为昨天。保持只读，无业务写入。'},
    {'id': 'X03', 'category': 'history_relative_day', 'turns': [
        '请查最近7天已经完成的训练，按日期列每次用时。',
        '时间范围改成前天，只要已完成的日期与每次耗时。'],
     'acceptance': '第一轮前天30分钟和昨天12分钟；第二轮仅前天30分钟，范围确实为前天。保持只读，无业务写入。'},
]}
(OUT / 'tasks-extra.json').write_text(json.dumps(extra, ensure_ascii=False, indent=2), encoding='utf-8')
verify = (ROOT / 'scripts/agent_experience/verify_persistence.py').read_text(encoding='utf-8')
verify = verify.replace('assert os.environ["DATABASE_URL"].endswith("fitness-experience-20261108-pg:5432/experience_live_retry_test")',
    'assert os.environ["DATABASE_URL"].split("@", 1)[-1] in {"fitness-experience-20261108-pg:5432/query_fix_core_test", "fitness-experience-20261108-pg:5432/query_fix_extra_test"}')
verify = verify.replace('"stale_confirm_rejections": 4', '"stale_confirm_rejections": sum(len(t.get("stale_confirm_attempts", [])) for t in result["tasks"].values())')
(OUT / 'verify_persistence.py').write_text(verify, encoding='utf-8')
print(json.dumps({'core_tasks': 16, 'extra_tasks': 6, 'core_unchanged': (OUT / 'tasks-core.json').read_bytes() == (FROZEN / 'tasks.json').read_bytes()}))
