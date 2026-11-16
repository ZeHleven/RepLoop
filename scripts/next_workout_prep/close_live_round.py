"""Close the bounded real-model investigation without replacing failed evidence."""
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-next-workout-prep-20261110'
OUT = ROOT / 'artifacts/agent-dev-notes/next-workout-prep-20261110'


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
assert head == '929b84dfd91df0daa51cc346ec7913e0bbe23539'
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
tested = load(OUT / 'ci3/source-manifest.json')
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in tested.items())
suite = ET.parse(OUT / 'ci3/backend-tests.xml')
assert len(list(suite.iter('testcase'))) == 2112 and not list(suite.iter('failure')) and not list(suite.iter('error'))
checks = load(OUT / 'ci3/checks.json')
assert len(checks) == 8 and all(c['exit_code'] == 0 for c in checks)
first = load(OUT / 'live-01/review.json')
replay = load(OUT / 'live-03-answer/result.json')
post = load(OUT / 'live-03-answer/db-postcheck.json')
assert first['provider_http_attempts'] == 9 and len(replay['provider_calls']) == 1
assert replay['provider_calls'][0]['status_code'] == 200 and replay['business_unchanged']
assert post['charge_states'] == {'settled': 9, 'http_error_estimate': 1}
assert post['known_actual_nano'] == 51146000 and post['budget_spent_nano'] == 169114000
assert replay['pending_runs'] == 0 and post['proposals'] == 0
assert load(OUT / 'live-03-answer/source-verification.json')['before_after_equal']
assert not (OUT / 'live-02-answer/provider-events.jsonl').exists()

patch = subprocess.check_output(['git', 'diff', '--binary', '7d2315ca6f3ee61b0ab7204d1c37d697abafc372', head], cwd=SOURCE)
patch_path = OUT / 'candidate-929b84d.patch'
assert not patch_path.exists()
patch_path.write_bytes(patch)
review = {'source_commit': head, 'scope': replay['scope'], 'protocol_fix_verified': True,
          'provider_http_attempts': 1, 'known_estimated_cny': 0.00645,
          'elapsed_ms': replay['elapsed_ms'], 'timing_scope': 'plan read plus answer rewrite; excludes router, initial execution, API and phone rendering',
          'business_unchanged': True, 'full_task_acceptance': None,
          'findings': ['Returned an answer using the actual synthetic plan and next-workout data.',
                       'This reply says not to force repetitions; it does not establish a fix for P01 advice quality.',
                       'The sentence 本周还没有完成记录 follows current-plan context but should state plan association explicitly; unrelated completed history exists.'],
          'limitations': ['Only the plan is re-read; initial model messages and next-workout observation are replayed from P02.',
                          'P03 multi-turn and P04 no-active-plan live cases remain unexecuted.',
                          'No phone retest or deployment.']}
write(OUT / 'live-03-answer/review.json', review)
report = load(OUT / 'validation.json')
report['offline_phase_415fe17'] = {'provider_http_calls': 0, 'cost_cny': 0, 'full_tests': 2110,
                                 'github_ci': 'success; evidence github-ci-jobs-success.json'}
report.update(status='protocol_fix_verified_advice_quality_and_remaining_live_cases_open',
    candidate_commit=head, clean_source=True, candidate_patch=patch_path.relative_to(ROOT).as_posix(),
    candidate_patch_sha256=hashlib.sha256(patch).hexdigest(), provider_http_calls=10,
    cost_cny=None, known_estimated_cny=0.051146, unknown_cost_budget_occupancy_cny=0.117968,
    conservative_total_budget_occupancy_cny=0.169114,
    test_database_status='both_dedicated_databases_stopped_data_retained',
    full_task_live_gate_passed=False, deployed=False)
report['tests']['protocol_before_verified'] = {'failed': 1, 'passed': 1, 'reason': 'actual HTTP request lacked thinking disabled'}
report['tests']['protocol_after'] = {'passed': 103, 'warnings': 6}
report['tests']['ci_protocol_fix'] = {'passed': 2112, 'failed': 0, 'errors': 0, 'warnings': 869, 'new_required_read_tests_total': 25}
report['checks_protocol_fix'] = checks
report['live'] = {'original_batch': first, 'targeted_replay': review, 'pending_runs': 0,
                 'model_charge_count': 10, 'application_budget_spent_nano': 169114000,
                 'application_budget_reserved_nano': 0, 'charge_note': 'HTTP400 is conservatively charged as http_error_estimate; observer retains its unknown-usage occupancy. Not a provider invoice.'}
report['preparation_failures'].extend([
    'New HTTP tests initially reused a fixed email, omitted final evidence guard, and expected HTTP errors to block instead of using existing http_error_estimate accounting; corrected fixtures/assertions before app change. All attempts retained.',
    'First post-fix command named a nonexistent test file; collected zero tests. Corrected command ran 103 tests.',
    'First targeted replay preflight omitted API intent_confidence to model confidence mapping; failed before any HTTP. Corrected observer and used a new evidence directory.'])
report['github'].update(head=head, ci_status='pending')
report['limits'] = [
    'Original phone tool trace remains unavailable; exact online omission vs read failure is unproven.',
    'Live batch has one partial answer, one failed answer, and two unexecuted tasks; it did not pass.',
    'The new candidate passed one targeted real final-answer replay, not a complete fresh API/worker task batch.',
    'P01 advice quality and explicit plan-associated completion wording remain open; prompts not changed to fit seen replies.',
    'P03/P04 live coverage and phone retest remain open; no production accuracy or P95 can be inferred.',
    'Unknown usage remains within the cumulative 30 HTTP / 0.50 CNY budget as a conservative occupancy.']
write(OUT / 'validation.json', report)

note = ROOT / 'docs/agent/dev-notes/next-workout-prep-20261110.md'
text = note.read_text(encoding='utf-8').replace(
    '真实模型首批未通过；接口修复后2112项离线验收通过，定向真实答复验证待做，未部署。',
    '真实模型首批未通过；929b84d接口修复后2112项离线及1次定向真实答复通过，建议质量与剩余场景仍开放，未部署。')
text += '''

## 真实复测收尾（不记完整任务通过）

最终候选929b84dfd91df0daa51cc346ec7913e0bbe23539，草稿PR #48已更新，未合并或部署。原415fe17批次封存：P01有建议但两处质量问题，记部分；P02补读成功后HTTP400，记失败；P03/P04未执行。原2次等待为8.33/7.28秒，其中后一条返回失败文案；不能按成功响应速度或生产P95宣传。

定向回放首次启动在构造IntentResolution时缺confidence字段，发生在HTTP前，0模型调用；已将API的intent_confidence映射到内部confidence，旧启动保留live-02-answer，新输出live-03-answer。新候选只重放P02已保存的模型消息及下一练观察，实际读取该合成账号的当前计划，然后发出唯一一次真实答复请求。HTTP200，thinking=disabled，无tools绑定，成功提供建议；这一步含补读与答复耗时2317.571毫秒，不含原意图识别、前序模型、API轮询或手机渲染。该样本不是完整任务、盲测或手机验收。新答复“不必硬追次数”不能证明P01的普通建议质量已修复；“本周没有完成记录”还应明确只限当前计划关联记录。

累计10次HTTP：9条用量齐全估算¥0.051146，原400未知占额¥0.117968，合计保守占用¥0.169114，低于30次/¥0.50授权；供应商实际最终费用仍未知。数据库账本为9 settled + 1 http_error_estimate，应用把错误的完整上界纳入spent估算，观察器把该部分列为未知占额，不能重复相加。两条Run、三条原工具审计、零提案保持，定向回放没有另建Run/审计；完整业务快照前后一致，0待处理Run，356份应用源码一致。两台本轮测试库已停止，保留全部数据。

验收命令：`scripts/next_workout_prep/run_tests.py protocol-before-verified tests/test_required_read_completion.py::test_rewrite_http_contract_and_billing`（未改应用1失败1通过）；`run_tests.py protocol-after-verified tests/test_required_read_completion.py tests/test_model_budget.py tests/test_agent_controller.py`（103通过）；`run_ci.py ci3`（2112通过及8门禁）；`launch_answer_replay.py`（新目录、冻结SHA、最多1HTTP，禁止重复）。源文件、模型原文、HTTP请求、工具消息、SQL快照、账本、所有失败尝试均在本轮证据目录，汇总validation.json。公开源码扫描通过。

后续优先处理普通准备建议的质量边界，再补P03多轮承接、P04无活动计划以及手机第5/6项。当前不具备完整上线验收结论；本地公众号草稿已更新，沿用合成机制图并说明真实模型协议失败，不公开用户截图或原始数据。
'''
note.write_text(text, encoding='utf-8')

issues = ROOT / 'docs/agent/dev-notes/issues.md'
text = issues.read_text(encoding='utf-8')
head_line, tail = text.split('\n', 1)
issues.write_text(head_line + '\n\n**2026-11-10｜QUERY-20261110-02｜接口修复与定向复验完成，质量问题开放。** 原批次1部分/1失败/2未跑，不通过；929b84d仅补读答复关闭thinking，103相关、2112完整离线及1次真实答复回放通过。累计10次HTTP，已知估算¥0.051146、未知占额¥0.117968，保守合计¥0.169114。普通建议的重量强度/变形凑次数问题、多轮和无计划真实覆盖、手机复验仍待做；PR #48草稿，未部署。两测试库已停，原失败与素材留档。[记录](next-workout-prep-20261110.md)。\n' + tail, encoding='utf-8')
index = ROOT / 'docs/agent/dev-notes/README.md'
text = index.read_text(encoding='utf-8').replace('下一练准备建议：本地补齐修复完成，2110项通过；真实模型与手机复验待做',
    '下一练准备建议：真实复测发现接口400，929b84d修复并定向验证；2112项通过，建议质量与剩余实测开放')
index.write_text(text, encoding='utf-8')

release = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json'
state = load(release)
state['deployment']['status'] = 'phone_advice_failure_observed_new_fix_not_deployed'
state['phone']['fix_candidate'] = {'commit': head, 'pr': 'https://github.com/ZeHleven/RepLoop/pull/48',
    'status': 'draft_protocol_replay_passed_live_task_gate_incomplete', 'deployed': False,
    'evidence': 'artifacts/agent-dev-notes/next-workout-prep-20261110/validation.json'}
write(release, state)
print(json.dumps({'candidate': head, 'http_calls': 10, 'known_estimated_cny': 0.051146,
                  'unknown_occupancy_cny': 0.117968, 'full_task_gate': False, 'deployed': False}))
