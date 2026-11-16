"""Freeze old regressions, new wording and seeded failure-context measurement."""
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).parent
OBS=HERE/'observer'; OBS.mkdir(exist_ok=False)
OLD=ROOT/'scripts/agent_query_fix/observer'
for name in ['capture.py','metrics.py','run_baseline.py','tasks-core.json','verify_persistence.py']:
    shutil.copy2(OLD/name,OBS/name)
runner=(OBS/'run_baseline.py').read_text(encoding='utf-8')
runner=runner.replace('AgentRun, AgentToolCall, AgentProposal','AgentRun, AgentToolCall, AgentProposal, AgentMessage')
anchor='                for index, item in enumerate(fixture["plan"]["monday"]):'
assert runner.count(anchor)==1
runner=runner.replace(anchor,'''                for index, item in enumerate(case.get("seed_history", [])):
                    # Synthetic recovery fixture: imported historical text, no
                    # fabricated successful query or server task identifier.
                    db.add(AgentMessage(conversation_id=conv.id, run_id=None,
                        role=item["role"], content=item["content"], content_data={},
                        created_at=datetime.now(timezone.utc)-timedelta(seconds=10-index)))
'''+anchor)
runner=runner.replace('"acceptance": case["acceptance"], "turns": []','"acceptance": case["acceptance"], "sample_role": case.get("sample_role", "original_core"), "seeded_history": case.get("seed_history", []), "turns": []')
(OBS/'run_baseline.py').write_text(runner,encoding='utf-8')
verify=(OBS/'verify_persistence.py').read_text(encoding='utf-8').replace('("query_fix_core_test", "query_fix_extra_test", "query_fix_c2_core_test", "query_fix_c2_extra_test")','("history_intent_core_test", "history_intent_extra_test")')
(OBS/'verify_persistence.py').write_text(verify,encoding='utf-8')
contract=json.loads((OLD/'tasks-extra-final.json').read_text(encoding='utf-8'))
contract['authorship']='Same implementer authored and reviews; not blind. Three previous X tasks are development regressions; R01 is seeded old-failure recovery; N01-N03 are new wording frozen before any new model output.'
for case in contract['cases']:case['sample_role']='known_regression'
old=json.loads((ROOT/'artifacts/agent-dev-notes/history-intent-release-20261108/original-x01.json').read_text(encoding='utf-8'))['task']
contract['cases'] += [
 {'id':'R01','category':'empty_task_recovery','sample_role':'seeded_recovery',
  'seed_history':[{'role':'user','content':old['turns'][0]['message']},{'role':'assistant','content':old['turns'][0]['response']['reply']}],
  'turns':[old['turns'][1]['message']], 'acceptance':'合成导入上轮失败原文，服务端无任务ID；首个意图尝试采用new且无空ID错误，仅查询昨天和今天的已完成训练，交付昨天12分钟；无业务写入。历史失败不计本轮模型调用或通过任务。'},
 {'id':'N01','category':'new_completion_wording','sample_role':'new_wording',
  'turns':['请把最近7天里已经完成过的训练按天列出来，附上各次用时。','查询范围改为最近两天，只显示已完成的日期和每次时长。'],
  'acceptance':'首轮前天30分钟和昨天12分钟；次轮范围缩为昨天至今天，仅昨天12分钟，排除进行中场次；无业务写入。'},
 {'id':'N02','category':'new_completion_wording','sample_role':'new_wording',
  'turns':['我想看看近三天做过并已完成的训练，各场是哪天、用了多久？'],
  'acceptance':'近三天范围，前天30分钟和昨天12分钟，不包括今天进行中场次，无业务写入。'},
 {'id':'N03','category':'new_completion_wording','sample_role':'new_wording',
  'turns':['请展示昨天完成过的训练：训练日期、每场耗时。','日期改为前天，只显示已完成场次的日期与耗时。'],
  'acceptance':'首轮仅昨天12分钟；次轮仅前天30分钟；日期范围与已完成状态正确，无业务写入。'},
]
(OBS/'tasks-extra.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
launcher=(ROOT/'scripts/agent_query_fix/launch_live.py').read_text(encoding='utf-8')
launcher=launcher.replace('.tmp/agent-query-fix-20261108','.tmp/agent-history-intent-20261108').replace('artifacts/agent-dev-notes/query-history-fix-20261108','artifacts/agent-dev-notes/history-intent-release-20261108')
launcher=launcher.replace("    parser.add_argument('--candidate', type=int, choices=(2,), default=2)\n",'')
launcher=launcher.replace('5_000_000_000','3_000_000_000').replace('160 - attempts','120 - attempts')
launcher=launcher.replace("label = f'c{args.candidate}-{args.suite}'","label = args.suite")
launcher=launcher.replace('query_fix_c{args.candidate}_{args.suite}_test','history_intent_{args.suite}_test').replace('fitness-query-fix-live-','fitness-history-intent-live-')
launcher=launcher.replace('tasks-extra-final.json','tasks-extra.json')
(HERE/'launch_live.py').write_text(launcher,encoding='utf-8')
print('Frozen: original16 tasks; known3 + seeded recovery1 + new wording3, 34 live Runs total.')
