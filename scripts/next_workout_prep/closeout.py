"""Summarize retained attempts and freeze the locally tested candidate."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from packaging.requirements import Requirement

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.tmp/agent-next-workout-prep-20261110'
OUT=ROOT/'artifacts/agent-dev-notes/next-workout-prep-20261110'
def load(p): return json.loads(p.read_text(encoding='utf-8'))
def write(p,obj): p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
checks=load(OUT/'ci2/checks.json')
assert len(checks)==8 and all(row['exit_code']==0 for row in checks)
assert load(OUT/'ci2/result.json')['exit_code']==0
suite=ET.parse(OUT/'ci2/backend-tests.xml').getroot()
cases=list(suite.iter('testcase'))
assert len(cases)==2110 and not list(suite.iter('failure')) and not list(suite.iter('error'))
new_cases=[x for x in cases if x.attrib['classname'].endswith('test_required_read_completion')]
assert len(new_cases)==23
tested=load(OUT/'ci2/source-manifest.json')
assert all(hashlib.sha256((SOURCE/p).read_bytes()).hexdigest()==h for p,h in tested.items())
before=load(OUT/'before-manifest.json')['source_files']
modified=[p for p,h in before.items() if hashlib.sha256((SOURCE/p).read_bytes()).hexdigest()!=h]
assert len([p for p in modified if p.startswith('backend/app/')])==4
assert not any(p.startswith('backend/alembic/') for p in modified)
errors=Counter(e.attrib.get('message','') for e in ET.parse(OUT/'ci/backend-tests.xml').iter('error'))
assert len(errors)==1 and sum(errors.values())==100
versions={k.lower().replace('_','-'):v for k,v in load(OUT/'ci2/dependencies.json').items()}
requirements=[Requirement(s) for s in (SOURCE/'backend/requirements.txt').read_text(encoding='utf-8').splitlines() if s.strip() and not s.startswith('#')]
assert all(versions[r.name.lower().replace('_','-')] in r.specifier for r in requirements)
report={
    'issue':'QUERY-20261110-01','base':'7d2315ca6f3ee61b0ab7204d1c37d697abafc372',
    'status':'local_validation_complete_remote_review_and_live_verification_pending',
    'source_path':SOURCE.relative_to(ROOT).as_posix(), 'application_files_changed':[p for p in modified if p.startswith('backend/app/')],
    'tests':{'before_first':{'application_omission_failures':2,'new_fixture_schema_errors':2},
        'before_corrected_fixture':{'failed':4,'passed':0},'after_core':{'passed':4},
        'targeted':{'passed':214,'failed':2,'reason':'historical date replay did not freeze recorded business day'},
        'unchanged_baseline_history':{'passed':49,'failed':2,'same_failure_as_candidate':True},
        'ci_first':{'passed':2010,'errors':100,'error_categories':dict(errors)},
        'ci_final':{'passed':2110,'failed':0,'errors':0,'warnings':869,'new_tests':23}},
    'checks':checks,'pinned_dependency_requirements_satisfied':len(requirements),
    'intent_rules':{'passed':39,'total':39},'scripted_tasks':{'passed':15,'total':15},
    'multiturn':{'passed_gate':True,'scope':'5 multi-turn tasks plus SQL isolation'},
    'proposal_revisions':{'passed_gate':True,'scope':'8 synthetic API/worker/SQL tasks'},
    'public_source_scan':{'passed':True,'command':'python scripts/check_public_secrets.py'},
    'provider_http_calls':0,'cost_cny':0,'production_writes':0,'deployed':False,
    'phone':{'samples':5,'user_reported_correct':4,'visible_delivery_failure':1,'latency_samples':0},
    'limits':['The phone tool trace is unavailable: omission versus failed read is not proven for that exact run.',
        'Scripted-model replay validates execution, not real-model answer quality or phone performance.',
        'Parameterized missing reads, failed reads and insufficient budgets retain the guard.',
        'Direct recovery can add one answer-only model request; its live latency/cost is unmeasured.',
        'No live gate, deployment or phone retest in this round yet.'],
    'diagram':{'path':'docs/agent/dev-notes/assets/next-workout-prep-20261110/required-read-flow.png',
        'type':'synthetic_flow_diagram_not_product_screenshot','visually_reviewed':True},
    'preparation_failures':['clone --branch main against detached source; retried from HEAD',
        'Docker not started; started explicitly', 'Docker address pool exhausted; checked subnets and created internal 10.253.50.0/28',
        'First plan test fixture used wrong schema; corrected fixture before application edits',
        'First CI used a container hostname rejected by the localhost-only guard; reran unchanged source via shared network namespace',
        'One report-summary read defaulted to Windows GBK; corrected reader to UTF-8, no test rerun']}
write(OUT/'validation.json',report)
note=ROOT/'docs/agent/dev-notes/next-workout-prep-20261110.md'
text=note.read_text(encoding='utf-8').replace('修改前登记，诊断与离线复现中。','本地修复与完整离线验收完成；未部署，真实模型与手机复验待做。',1)
text=text.replace('计划在未改7d基线重跑确认，随后仅把这两条回放的时钟绑定到原始夹具 `as_of`','未改7d基线同样49通过、2失败，已仅把这两条回放的时钟绑定到原始夹具 `as_of`')
text += '''

## 最终本地实现与验收

四个运行文件：`agent_evidence_contract.py`筛选可补的必需缺项；`agent_controller.py`复用正式工具/审计在计划结束前补齐；`agent_runtime.py`接入直接路径并仅追加一次无工具的回答，失败SELECT回滚到保存点、错误结果不渲染数据卡；`agent_trace.py`不把控制器生成的工具信封算成模型调用，并标注回答重写失败。未修改意图分类、真实日期校验、迁移或小程序。

最终后端 **2110/2110通过，869条既有弃用等警告**，含本轮23项新增回归；规则意图39/39，脚本单轮15/15，多轮5任务与SQL隔离门禁通过，提案修订8任务门禁通过；0032升级→0023降级→head升级通过。复用镜像中的20项声明依赖均满足当前requirements；公开源码检查通过。没有付费模型请求，费用0元。

失败均保留：首轮4项中2项是新夹具schema错误，修正夹具后原应用4项真实遗漏回放失败；相关回归214通过/2个旧日期夹具失败，未改基线亦49通过/相同2失败；首轮完整2010通过/100项测试库地址保护setup错误，改为测试容器共享独立数据库网络命名空间，通过127.0.0.1访问后，保持应用不变重新完整2110通过。没有删除错误样本或放宽保护。

API与PostgreSQL合成检查确认两次正式读取和审计落库、只返回本人的计划、计划/动作/训练记录/提案数前后保持；缺计划记录是有效否定证据，读取超时或SQL失败不会自动重试。该验证使用脚本模型，不能作为真实建议质量或手机耗时成绩。实际现场工具轨迹仍缺，尚不能排除那次线上为读取报错。

复现：`python scripts/next_workout_prep/run_tests.py <新标签>`（需本轮独立容器）；正式仓库可按既有CI配置运行 `python -m pytest -q tests/test_required_read_completion.py` 与完整CI。各次命令和退出码见本轮证据目录，各回放保存回答、工具消息和执行轨迹；`validation.json`汇总全部尝试，`ci2/source-manifest.json`冻结实测源码。旧071发布检出保持干净。

本地公众号草稿：[草稿](next-workout-prep-20261110-wechat-draft.md)。[配图](assets/next-workout-prep-20261110/required-read-flow.png)为已目视检查的合成机制图，不是手机截图；用户原件仅私有本地留档。未发布。

后续：以该候选进行有界真实模型验证、审查与发布，再复验第五项并采集第六项。直接路径补齐可能增加一次回答调用，实际延迟与费用尚未测量；不据离线通过宣布线上已恢复。
'''
note.write_text(text,encoding='utf-8')
shutil.copy2(note,SOURCE/'docs/agent/dev-notes'/note.name)
entry='**2026-11-10｜QUERY-20261110-01｜本地修复完成。** 下一练建议的必需读取遗漏已补有限恢复；原4项回放失败→4通过，新增23项、完整2110项通过，迁移与离线Agent门禁通过。两项旧日期回放漂移在未改基线同样失败，固定样本时钟；首轮100项测试库保护setup错误修正运行方式后全绿，失败留档。本轮0模型HTTP/¥0，4项用户反馈正确+1截图失败的手机事实保持；未部署、未做真实模型及手机复验。草稿和合成图仅本地保存。[记录](next-workout-prep-20261110.md)。'
for root in [ROOT,SOURCE]:
    p=root/'docs/agent/dev-notes/issues.md'; text=p.read_text(encoding='utf-8'); heading,rest=text.split('\n\n',1)
    p.write_text(heading+'\n\n'+entry+'\n\n'+rest,encoding='utf-8')
index=ROOT/'docs/agent/dev-notes/README.md'
text=index.read_text(encoding='utf-8').replace('下一练准备建议失败：第5项短句截图已留档，必需读取补齐修复进行中','下一练准备建议：本地补齐修复完成，2110项通过；真实模型与手机复验待做')
index.write_text(text,encoding='utf-8')
review=ROOT/'docs/agent/reviews/2026-11-08-history-intent-release.md'
with review.open('a',encoding='utf-8') as stream:
    stream.write('\n\n2026-11-10追加：第5项实际短句“给我下一次训练的准备建议”出现资料不全回退，截图显示未交付建议。累计4项用户反馈正确、1项截图可见失败，手机计时0；第6项未测。新轮已修复可复现的遗漏必需读取缺口、完整2110项通过，未部署；不覆盖或改写原始失败。见 `docs/agent/dev-notes/next-workout-prep-20261110.md`。\n')
pr_body='''只读建议可能已展示“下一练”卡片，收尾时却因漏读当前计划被证据检查替换为失败提示。现在直接执行和计划执行都会在已有权限、工具/模型预算内补齐尚未尝试的无参数必读项，并将真实结果交给最终答复；已完整、已失败、需猜参数、澄清和安全停止的任务不补查。补读保留正式工具审计，失败读取不再显示为数据卡，服务端工具信封不计作模型调用。

另修正两条历史回放测试未固定业务日的问题：原7d基线同样失败，现使用原始样本as_of，生产日期校验与样本答案均保持。

验证：新增23项；完整后端2110通过；迁移往返、规则39/39、脚本单轮15/15、多轮及提案修订门禁通过。真实PostgreSQL/API检查确认读取本人计划、审计落库和业务数据不变。所有外部模型调用为0。

此PR保持草稿：实际手机请求未提供工具轨迹，尚不能确定该次是漏读还是读取错误；真实模型门禁、部署与手机复验待完成。手机首轮仍记4项用户反馈正确、1项截图失败，不能据离线回放宣布线上恢复。
'''
(OUT/'pr-body.md').write_text(pr_body,encoding='utf-8')
print(json.dumps({'backend_tests':len(cases),'new_tests':len(new_cases),'ci_checks':len(checks),'model_calls':0,'status':report['status']}))
