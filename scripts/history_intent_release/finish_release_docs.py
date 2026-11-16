"""Close local rounds against the successful gate; leave deployment unclaimed."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
NOTES = ROOT / 'docs/agent/dev-notes'
status = json.loads((OUT / 'release-status.json').read_text(encoding='utf-8'))
gate = status['formal_gate_verified']
assert gate['passed'] and status['package']['build_metadata']['build_commit'] == gate['candidate_sha']
assert status['main_ci_run']['conclusion'] == 'success'
sha = gate['candidate_sha']

def append_once(path, heading, body):
    value = path.read_text(encoding='utf-8').split('\n' + heading)[0]
    path.write_text(value.rstrip() + '\n\n' + heading + '\n\n' + body.rstrip() + '\n', encoding='utf-8')

append_once(NOTES / 'history-intent-release-20261108.md', '## 发布门禁与当前交接状态', f'''
查询修复PR #46及评测器修复PR #47均已合并，最终完整SHA为`{sha}`。原历史查询实测代码不变：121份backend/app逐字一致；最终GitHub后端2087通过/868警告，小程序与容器构建通过。

原a65a3ff门禁的3/33由评测器缺计费账号造成，30项在费用保护层受阻；保留为失败，不能当作真实语义识别准确率。评测器只补隔离合成账号和既有计费上下文，没有修改题目或判分条件。新SHA正式门禁路由33/33（关键9/9）、两轮饮食各31/31，未确认写入、意外提案、优化器不可用均0。详见[门禁修复轮次](release-route-billing-20261108.md)。

同SHA后端包已准备，source_dirty=false；207个非元数据包文件与干净检出一致。打包前首次指纹检查发现两份测试因Windows检出转成CRLF，已证明Git对象一致、LF规范化后逐字一致且不入包；唯一后续实际代码差异为评测脚本，故清单为351/354字节一致，不能仍写354/354。失败检查及证据保留。

本地费用仍184次/估算¥1.227114；最新正式路由另有36条已结算记录/估算¥0.305162、0未决预留。两次正式工作流的饮食部分旧脚本未提供完整费用，发布总费用未知，不填0或声称全部成本只有1.23元。

生产尚未部署，手机样本0；已提供[部署包与交接说明](deploy-history-intent-release-20261108.md)和[手机采集单](history-intent-release-20261108-phone-checklist.md)，等待用户执行控制台步骤并回报部署号、日志SHA与旧实例数。电脑控制工具因无法可靠识别Windows当前URL而停止，未绕过。最后本地数据库停止时间2026-11-09T07:37:01Z，所有数据与失败证据保留。草稿、指标示意及流程图源已准备，未发布公众号。
''')

append_once(NOTES / 'release-route-billing-20261108.md', '## 最终正式验收与收尾', f'''
PR #47已合并为`{sha}`，其PR/push及合并主线CI全部通过，后端2087项、868警告，迁移与Agent脚本、前端合同/类型/构建、后端容器均通过。121份backend/app与f4d388f实测运行代码相同，33题及passed表达式AST保持；没有重测或改写原23项本地任务分数。

[新SHA正式门禁]({gate['url']})只执行一次并成功：语义路由33/33、关键9/9，ordinary_rules_fallback=0；两轮饮食分别10/10原句、21/21同义，未确认写入/意外提案/优化器不可用均0。报告candidate_sha逐项与最终完整SHA核对通过。

路由新增账本显示36条预留请求全部settled，估算¥0.305162、reserved=0、blocked=false、complete=true。这里是应用保守费率，非供应商账单；饮食部分及原失败流程的完整费用仍未知，不能汇总为0。原门禁42577336410的脱敏结果与完整日志保持，不拿后续成功覆盖旧失败。

本轮不付费回归50通过；正式门禁真实调用与之分开。before/after日志、源码指纹、CI日志、两份正式JSON、GitHub artifact元数据及完整正式日志均在artifacts/agent-dev-notes/release-route-billing-20261108。专用数据库最终已停（2026-11-09T07:37:01Z），数据保留。

包由同SHA干净检出生成，核验见[发布交接](deploy-history-intent-release-20261108.md)。两份测试的CRLF检出差异已用相同Git对象证明，不属于运行代码变化，首次失败检查单独留档。生产部署和手机采集仍待用户完成，公众号未发布。
''')

draft = NOTES / 'history-intent-release-20261108-wechat-draft.md'
append_once(draft, '## 后续发布验证补记', '''
正式发布门禁又暴露了一个评测器问题：路由脚本缺少计费账号上下文，30项被费用保护挡在请求发送之前。修复时只接入隔离合成账号，没有关闭费用保护，也没有改题。GitHub完整测试增至2087项；最终门禁路由33/33、两轮饮食各31/31通过，原失败完整保留。

同SHA后端包已准备，尚未部署，也还没有手机样本。此前约1.23元仅指本地两候选；最新正式路由另估算约0.31元，饮食门禁未输出完整费用，不能称发布总成本为1.23元。本稿未发布公众号。
''')
append_once(NOTES / 'release-route-billing-20261108-wechat-draft.md', '## 正式复测结果补记', '''
修复后完整GitHub测试2087项通过。对新合并SHA的正式门禁只运行一次：路由33/33、关键9/9，全天饮食两轮各31/31；所有未确认写入与意外提案均为0。新路由36条账本全部结算，保守估算约0.31元。旧失败仍留档，不能把3/33改写为模型成绩或抹去。

现在后端包已按同一SHA生成并核验，生产还没有切换，手机反馈仍为0。本地草稿和配图源已准备，公众号尚未发布。
''')

issue = f'**2026-11-08｜EVAL-20261108-01完成。** PR #47合并为{sha[:7]}；2087项后端及小程序CI通过。新正式门禁路由33/33、关键9/9、两轮饮食各31/31，0未确认写入/意外提案/优化器不可用。121份Agent运行源码、题目与判分保持。原门禁受阻失败留档；新路由36条结算/估算¥0.305162，饮食费用未知。已打包核验，生产未部署、手机样本0。[收尾](release-route-billing-20261108.md) / [部署交接](deploy-history-intent-release-20261108.md)。'
p = NOTES / 'issues.md'
text = p.read_text(encoding='utf-8')
if issue not in text:
    first, rest = text.split('\n', 1)
    p.write_text(first + '\n\n' + issue + '\n' + rest, encoding='utf-8')
p = NOTES / 'README.md'
text = p.read_text(encoding='utf-8').replace('正式路由评测接入计费上下文：5项改前失败，修复后相关50项通过，运行代码与判分不变',
    '正式路由门禁已修复并通过：33/33，饮食两轮各31/31；2087项CI通过，待部署')
p.write_text(text, encoding='utf-8')
status['deployment']['handoff_requested'] = True
status['deployment']['guide'] = 'docs/agent/dev-notes/deploy-history-intent-release-20261108.md'
status['deployment']['blocked_by'] = 'Computer-use tool stopped because current browser URL could not be reliably identified; no available Tencent deployment connector or CLI.'
status['phone']['checklist'] = 'docs/agent/dev-notes/history-intent-release-20261108-phone-checklist.md'
(OUT / 'release-status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'ready_sha': sha, 'backend_ci': 2087, 'route': '33/33', 'meal_passes': ['31/31', '31/31'], 'deployed': False, 'phone_samples': 0}))
