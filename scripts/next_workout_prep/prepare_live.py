"""Freeze the user-authorized 30-request / CNY 0.50 synthetic live probe."""
from pathlib import Path
import hashlib
import json
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from agent_eval import source_manifest as shared_observer_manifest
HERE=Path(__file__).parent; OBS=HERE/'observer'; OBS.mkdir(exist_ok=False)
OLD=ROOT/'scripts/history_intent_release/observer'
OUT=ROOT/'artifacts/agent-dev-notes/next-workout-prep-20261110'
for name in ['capture.py','metrics.py','run_baseline.py']:
    shutil.copy2(OLD/name,OBS/name)
runner=(OBS/'run_baseline.py').read_text(encoding='utf-8')
runner=runner.replace('fitness-experience-20261108-pg:5432/','fitness-prep-live-20261110-pg:5432/')
old='plan = WorkoutPlan(user_id=uid, name=fixture["plan"]["name"], goal="保持体重", days_per_week=1, duration_weeks=4)'
assert runner.count(old)==1
runner=runner.replace(old,old[:-1]+', is_active=not case.get("no_active_plan", False))')
needle='            return {"plan": [list(r) for r in planned], "meals": [[str(r[0]), *r[1:]] for r in meals],'
assert runner.count(needle)==1
runner=runner.replace(needle,'''            plan_states = (await db.execute(select(WorkoutPlan.id, WorkoutPlan.name, WorkoutPlan.is_active)
                .where(WorkoutPlan.user_id == uid).order_by(WorkoutPlan.id))).all()
            sessions = (await db.execute(select(WorkoutSession.id, WorkoutSession.trained_at, WorkoutSession.status, WorkoutSession.duration_min)
                .where(WorkoutSession.user_id == uid).order_by(WorkoutSession.id))).all()
            return {"plan_states": [list(r) for r in plan_states],
                    "sessions": [[r[0], str(r[1]), r[2], r[3]] for r in sessions],
                    "plan": [list(r) for r in planned], "meals": [[str(r[0]), *r[1:]] for r in meals],''')
(OBS/'run_baseline.py').write_text(runner,encoding='utf-8')
contract=json.loads((OLD/'tasks-core.json').read_text(encoding='utf-8'))
contract['authorship']='Implementer-authored development regression. Synthetic data, not blind and not a phone replay; actual production tool trace unavailable.'
contract['cohorts']=[{'id':'serial','client_concurrency':1}]
contract['cases']=[
    {'id':'P01','category':'next_prep_short','sample_role':'reported_utterance_new_conversation',
        'turns':['给我下一次训练的准备建议'],
        'acceptance':'基于真实读取的合成活动计划/下一练给出准备建议；不回退未取资料，不捏造计划动作、日期或训练量；无写入或未请求提案。'},
    {'id':'P02','category':'next_prep_explicit_readonly','sample_role':'original_protocol_utterance',
        'turns':['结合我已有的计划，给下一次训练的准备建议，先不要修改。'],
        'acceptance':'明确只读建议，结合实际计划/下一练；完整证据、无业务修改、无意外提案，不要求实际训练。'},
    {'id':'P03','category':'phone_sequence_synthetic','sample_role':'synthetic_context_sequence_not_phone_trace',
        'turns':['我当前的训练计划是什么？','请列过去一周完成过的训练，每场日期和用了多少分钟。',
            '范围收窄到最近两天，仍只要已完成的日期和用时。','时间范围改成前天，只要已完成的日期与每次耗时。',
            '给我下一次训练的准备建议'],
        'acceptance':'前四轮依次交付计划、前天30分钟和昨天12分钟、仅昨天12分钟、仅前天30分钟；末轮转为下一练准备建议，不继承前天历史范围；所有读取匹配实际来源，原计划/训练/饮食与提案不变。'},
    {'id':'P04','category':'no_active_plan','sample_role':'negative_control','no_active_plan':True,
        'turns':['给我下一次训练的准备建议'],
        'acceptance':'正确说明没有启用的训练计划，不把停用计划当活动计划，不虚构具体下一练；可提供明确标为一般性的准备建议或询问计划，无业务写入和提案。'},
]
(OBS/'tasks.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
record={'candidate':'415fe17387b8844f20622deaf194f90e02bcf67b','authorization':'允许，进行真实模型复测',
    'environment_source':'.env model configuration only; never application DB/auth credentials',
    'max_http_requests':30,'max_estimated_cny':0.50,'cases':4,'turns':8,'attempts_per_case':1,
    'one_batch_only':True,'deploy_authorized':False,'model_calls_started':0,
    'stop_conditions':['request/cost reservation cap','unknown usage/provider error','business date changes','source changes','per-turn timeout'],
    'shared_observer_files':shared_observer_manifest(),
    'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OBS.iterdir() if p.is_file()}}
(OUT/'live-preregistration.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
note=ROOT/'docs/agent/dev-notes/next-workout-prep-20261110.md'
with note.open('a',encoding='utf-8') as stream:
    stream.write('\n\n真实模型追加预登记：用户明确同意“允许，进行真实模型复测”，本轮单独上限30次HTTP/估算¥0.50，沿用.env模型配置，不部署。冻结415fe17，4任务8轮串行各一次：实际短句、新会话原长句、前四项查询后承接短句、无活动计划负例。采用隔离合成账号和TCP/API/worker/PostgreSQL，保存原始provider响应、工具与账本；错误、未知用量及预算超限停止，不自动整批重跑。输入、验收条件与观察器指纹见live-preregistration.json及scripts/next_workout_prep/observer/tasks.json。此补充不覆盖前面0模型调用的离线阶段记录。\n')
print(json.dumps({'cases':4,'turns':8,'requests_cap':30,'cny_cap':0.5,'calls_started':0}))
