"""Preserve phone evidence and pre-change source; never overwrite receipts."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-next-workout-prep-20261110'
OUT = ROOT / 'artifacts/agent-dev-notes/next-workout-prep-20261110'
PREVIOUS = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
BASE = '7d2315ca6f3ee61b0ab7204d1c37d697abafc372'
OUT.mkdir(parents=True, exist_ok=False)
def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
head = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
assert head == BASE
assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain']).strip()
paths = subprocess.check_output(['git', '-C', str(SOURCE), 'ls-files', 'backend'], text=True).splitlines()
write_json(OUT/'before-manifest.json', {'base': head, 'source_files': {
    p: hashlib.sha256((SOURCE/p).read_bytes()).hexdigest() for p in paths}})
subprocess.run(['git','-C',str(SOURCE),'archive','--format=zip',f'--output={OUT / "before-backend.zip"}',BASE,'backend'], check=True)
image_source = Path('C:/Windows/TEMP/codex-clipboard-bf8a1af6-0dcd-45cc-bec9-7c5b15b2d1c3.png')
image_target = OUT/'phone-071-05-original.png'
shutil.copy2(image_source, image_target)
receipt = {
    'sample_id': 'phone-071-05', 'task_number': 5,
    'task': '给我下一次训练的准备建议',
    'planned_task': '结合我已有的计划，给下一次训练的准备建议，先不要修改。',
    'protocol_match': 'shorter_utterance_variant',
    'reported_on_client_date': '2026-11-10', 'client_timezone': 'America/Los_Angeles',
    'raw_user_feedback': '这是什么问题', 'evidence_source': 'user_supplied_phone_screenshot',
    'screenshot': image_target.relative_to(ROOT).as_posix(),
    'screenshot_sha256': hashlib.sha256(image_target.read_bytes()).hexdigest(),
    'backend_commit_context': BASE, 'client_commit_context': BASE,
    'deployment_context': 'fitness-agent-api-071',
    'version_basis': 'Previously confirmed startup and phone preview; no per-run build proof in this screenshot.',
    'correctness': 'failed_advice_delivery_visible_in_screenshot',
    'completion': 'not_completed',
    'agent_response_text': '这次还没有完整取得当前计划，暂时无法可靠完成这项查询或评估。请稍后重试缺少的部分。',
    'visible_card': {'title':'下一练','plan_name':'增肌 · 1日训练计划',
        'schedule':'周3 · 7 个动作', 'visible_rows':[
            '飞鸟 · 8 组 × 8-12','器械推肩 · 4 组 × 8-12',
            '三头肌下压 · 4 组 × 8-12','杠铃弯举 · 4 组 × 8-12'],
        'hidden_exercises': None},
    'tool_trace': None, 'run_id': None, 'device': None, 'wechat_version': None,
    'network': None, 'business_date': None, 'sent_at': None,
    'first_visible_seconds': None, 'complete_visible_seconds': None,
    'corrections': None, 'satisfaction_1_to_5': None, 'unauthorized_writes_checked': None,
    'provider_http_calls': None, 'provider_cost_cny': None,
    'independently_reviewed': True, 'review_scope':'Visible advice-delivery failure only; not backend data correctness or tool execution.',
    'trial_selection_verified': False,
    'privacy':'Private original phone screenshot, not cleared for public publication. Public materials must be synthetic or redacted.',
    'limits':'No raw model or tool trace. Missing plan evidence is consistent with an omitted or failed read; screenshot alone does not distinguish. No latency or production accuracy estimate.'
}
write_json(OUT/'phone-observation.json', receipt)
phone_path=PREVIOUS/'phone-observation-05-20261110.json'
assert not phone_path.exists()
write_json(phone_path, receipt)
shutil.copy2(PREVIOUS/'final-integrity.json', PREVIOUS/'final-integrity-before-phone-observation-05.json')
state_path=PREVIOUS/'release-status.json'
shutil.copy2(state_path, OUT/'release-status-before-phone-05.json')
state=json.loads(state_path.read_text(encoding='utf-8'))
state['deployment']['status']='phone_advice_failure_observed_fix_in_progress'
state['deployment']['runtime_acceptance_passed']=False
phone=state['phone']
phone.update(status='four_user_correct_one_screenshot_failure', samples=5,
    independently_reviewed_samples=1, screenshot_observed_failed_samples=1,
    remaining_task_numbers=[6], failed_task_numbers=[5], pending_retest_task_numbers=[5],
    independent_review_scope='Sample 5 visible delivery failure only; no independent backend/run trace verification.')
phone['observation_evidence'].append(phone_path.relative_to(ROOT).as_posix())
write_json(state_path,state)
note='docs/agent/dev-notes/next-workout-prep-20261110.md'
shutil.copy2(ROOT/note,OUT/'PROTOCOL.md')
shutil.copy2(ROOT/note,SOURCE/note)
entry='**2026-11-10｜QUERY-20261110-01｜修改前登记。** 手机第5项短句“给我下一次训练的准备建议”显示下一练卡片却回退“未完整取得当前计划”，未交付建议；4项用户正确反馈、1项截图失败、计时0。已保存真实截图，线上工具轨迹尚缺；静态定位只读路径漏发必需读取时只有最终拦截、无有界补齐。基于7d独立分支先失败回归再修复，限定授权内无参数未尝试读取，保留失败/参数/预算边界；暂不付费或部署。[本轮计划](next-workout-prep-20261110.md)。'
for root in [ROOT,SOURCE]:
    issues=root/'docs/agent/dev-notes/issues.md'
    text=issues.read_text(encoding='utf-8'); title,rest=text.split('\n\n',1)
    issues.write_text(title+'\n\n'+entry+'\n\n'+rest,encoding='utf-8')
    index=root/'docs/agent/dev-notes/README.md'
    text=index.read_text(encoding='utf-8')
    text=text.replace('# 循练 Agent 开发笔记\n', '# 循练 Agent 开发笔记\n\n- [下一练准备建议失败：第5项短句截图已留档，必需读取补齐修复进行中](next-workout-prep-20261110.md)\n',1)
    text=text.replace('前4项查询获用户正确反馈，后2项及计时待采集','前4项用户反馈正确，第5项截图失败，第6项与计时待采集')
    index.write_text(text,encoding='utf-8')
update='2026-11-10更新：第5项实际输入为“给我下一次训练的准备建议”（原长句的短表达变体），截图显示“下一练”卡片，但正文提示未取得当前计划、未交付建议。现有5个观察：4项用户反馈正确、1项截图可见失败，手机计时0；第6项待采集，第5项待修复后复验。截图只支持交付失败，缺少该次run_id和工具轨迹，未证明漏读或工具错误是哪一种，也未独立核验写入。详见[修复记录](next-workout-prep-20261110.md)。'
for name in ['history-intent-release-20261108-phone-checklist.md','deploy-history-intent-release-20261108.md']:
    p=ROOT/'docs/agent/dev-notes'/name
    text=p.read_text(encoding='utf-8'); title,rest=text.split('\n\n',1)
    p.write_text(title+'\n\n'+update+'\n\n此前阶段记录（以下按时间保留）：\n\n'+rest,encoding='utf-8')
write_json(OUT/'preparation.json', {'base':BASE,'backend_files':len(paths),
    'model_http_calls':0,'production_writes':0,
    'setup_attempts':[{'command':'git clone --no-hardlinks --branch main <frozen-release> <new-clone>','exit_code':1,'reason':'Source has no main branch; no source mutation.'},
    {'command':'git clone --no-hardlinks <frozen-release> <new-clone>; git switch -c codex/agent-next-workout-prep-20261110','exit_code':0}],
    'screenshot_sha256':receipt['screenshot_sha256']})
print(json.dumps({'source':str(SOURCE),'base':BASE,'preserved_files':len(paths),'phone_samples':5,'model_http_calls':0}))
