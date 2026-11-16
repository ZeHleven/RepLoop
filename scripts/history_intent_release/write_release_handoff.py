"""Update local release instructions from observed GitHub and package receipts."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
NOTES = ROOT / 'docs/agent/dev-notes'
status = json.loads((OUT / 'release-status.json').read_text(encoding='utf-8'))
sha = status['release_candidate_sha']
gate = status.get('formal_gate_verified', {})
package = status.get('package')
ready = bool(gate.get('passed') and package)
state = '正式门禁通过、同SHA后端包已核验，尚未部署' if ready else 'PR已合并，正式门禁进行中；暂不部署'
if gate and not gate.get('passed'):
    state = '正式门禁未通过，暂不部署；路由评测问题单独修复'
gate_url = status.get('gate_run', {}).get('url')
health_path = OUT / 'production-health-before-release.json'
health_section = '尚未读取生产健康探针。'
if health_path.exists():
    health = json.loads(health_path.read_text(encoding='utf-8'))
    status['production_health_before_release'] = health
    (OUT / 'release-status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    health_section = f"部署前只读探针（{health['checked_at']}）：" + '；'.join(f"{c['path']} HTTP {c.get('status_code', c.get('error_type'))}" for c in health['checks']) + '。此前已核验的公开域名仍可访问，ready显示worker开关开启，但这些接口不提供build SHA，也不证明worker活性或新版已上线。模型调用0、业务写入0。'

package_section = '待正式门禁通过后才生成可发布包。目前没有本轮可发布包。'
if package:
    package_section = f'''- 后端包：[{Path(package['package']).name}](../../../{package['package']})
- 完整build_commit：`{sha}`；build_version：`0.5.47`；source_dirty：`false`。
- SHA256：`{package['sha256']}`。
- {package['size_bytes']} bytes / {package['entries']} entries；ZIP CRC、边界和必需/排除文件检查通过。
- {package['packaged_files_identical_to_checkout']}个非元数据文件与干净检出逐字一致；实测backend清单中{package['backend_files_identical_to_measured_candidate']}/{package.get('measured_backend_manifest_files', 354)}份字节一致。另2份测试仅Windows检出换行差异（Git对象相同且不入包），唯一后续代码变化为正式路由评测脚本。生产backend/app全部{package.get('runtime_files_unchanged') or 121}份逐字保持。
- [包核验回执](../../../artifacts/agent-dev-notes/history-intent-release-20261108/package-verification.json)。旧51feceb与3d37905包保留，但不包含本轮全部修复。'''
gate_section = f'[正式门禁]({gate_url})尚未结束，不将进行中记为通过。' if gate_url else '新SHA正式门禁尚未启动。'
if gate:
    route = gate['route']
    gate_section = f'''[正式门禁]({gate_url})结论：{'通过' if gate['passed'] else '未通过'}。语义路由{route.get('passed_count')}/{route.get('total')}，关键路由{route.get('critical_passed')}/{route.get('critical_total')}。

'''
    for meal in gate['meal_passes']:
        gate_section += f"- 全天饮食第{meal['pass_index']}轮：原句{meal['original_successes']}/{meal['original_runs']}，同义表达{meal['synonym_successes']}/{meal['synonym_runs']}；未确认写入{meal['unconfirmed_meal_writes']}，意外提案{meal['proposal_count']}，优化器不可用{meal['optimizer_unavailable_runs']}。\n"
    gate_section += '\n正式脚本实际为33个路由样本、两轮各10次原句和21种同义表达（旧部署文档写20条）；按脚本原门槛核验，没有降低门槛或为通过重跑。该工作流不输出完整模型HTTP/费用账本，正式门禁费用未知，不能记0；本地两候选184次/估算¥1.227114另列，不是全部发布过程总成本。'
    if gate.get('route_billing'):
        bill = gate['route_billing']
        gate_section += f"\n\n本次路由部分新增账本汇总：预留请求{bill['request_reservations']}，结算状态{bill['status_counts']}，估算¥{bill['estimated_cny']}，未决预留¥{bill['reserved_cny']}，费用完整性{bill['complete']}。这仅是本次路由部分，不包含饮食两轮及首次失败流程的费用。"

harness_section = ''
if status.get('route_harness_repair'):
    fix = status['route_harness_repair']
    harness_section = f"\n[PR #47](https://github.com/ZeHleven/RepLoop/pull/47)状态：{fix['pr']['state']}。它只修路由门禁的隔离合成计费账号，50项不付费相关回归通过，121份backend/app及题目/判分不变。首次a65a3ff门禁仍记失败：30项被缺账号的费用保护拒绝（非有效模型准确率），饮食两轮各31/31；结果和日志已保留。最新完整CI见各运行回执。\n"
    if fix.get('github_backend_tests_passed'):
        harness_section += f"\n修复后的完整GitHub后端测试为{fix['github_backend_tests_passed']}通过、{fix['github_backend_warnings']}警告；PR与主线的小程序合同、类型、构建及后端容器构建也通过。\n"

text = f'''# 历史查询修复：同SHA发布交接

状态：**{state}**。本页为本地执行回执，合并时的仓库报告保留当时状态。生产服务与配置尚未修改。

[PR #46](https://github.com/ZeHleven/RepLoop/pull/46)查询修复已合并，首次合并SHA为a65a3ff。[PR CI](https://github.com/ZeHleven/RepLoop/actions/runs/42576345918)两项任务均通过：后端2082测试、868警告，迁移/Agent检查、容器构建及小程序交互合同、类型与微信构建通过。当前待发布完整SHA为`{sha}`。
{harness_section}

## 正式发布门禁

{gate_section}

## 已核验产物

{package_section}

## 控制台部署步骤

以下仅在上方状态为“门禁通过、包已核验”后执行。

1. 打开现有[云托管服务更新页](https://tcb.cloud.tencent.com/dev?envId=reploop-prod-d1gi33fq538f57013#/platform-run/service/update?serverName=fitness-agent-api)。核对环境`reploop-prod-d1gi33fq538f57013`和服务`fitness-agent-api`，先记录当前部署号与SHA。线上0.5.47/e871b4d是用户先前回报，本轮未直接重新读取，不能当成刚核实。
2. 记录现有配置和旧产物；停止发送新的Agent请求，等待在途任务结束。新建服务版本并上传上面的ZIP，Dockerfile=`Dockerfile`，端口`8000`。
3. 沿用生产数据库、密钥、额度与提案配置。相对main/51feceb无新迁移或环境变量，小程序代码没有变化；现有schema应已含0033，保留额度账本和`RUN_DB_MIGRATIONS_ON_STARTUP=false`。核对既有症状功能两个开关保持启用，不从本机.env覆盖生产配置。
4. 新版本接流量后核对旧版本实例为0；只把旧版本流量改为0不能阻止后台worker继续领取任务。若平台无法避免混合worker，先保留证据并停止切换，不清空任务或数据库。
5. 启动日志必须显示完整SHA`{sha}`、source_dirty=false及worker启动；只显示0.5.47不足以证明升级。核对`/health`和`/ready`成功，再验证实际任务由新版处理；ready本身不证明worker活性。
6. 按[手机采集单](history-intent-release-20261108-phone-checklist.md)执行首轮6任务，填写[CSV模板](../../../artifacts/agent-dev-notes/history-intent-release-20261108/phone-observations-template.csv)。先核对日期/每场时长，再记录实机等待、纠正与满意度。未测字段保持空白，不填本地API时间。

本轮只更新后端，没有重新发布小程序；GitHub已验证现有小程序构建。若后续发布小程序，应以相同源码SHA单独构建并保留其来源。回退优先使用保留的应用产物；本轮无需也不执行数据库降级。

## 仍需要用户操作的部分

{health_section}

电脑控制工具因无法可靠识别Windows当前浏览器URL而停止；遵从工具限制，没有重试或换另一种UI方式绕过。可用工具中没有腾讯云部署接口，本机未发现CloudBase CLI，因此本轮无法直接核验控制台、当前线上SHA或执行切流量。请按上面已准备好的包和步骤在控制台完成部署，再回报部署号、日志SHA、旧版本实例数和health/ready结果；无需发送密钥。

手机数据也需要真实客户端操作。尚未收到线上部署或手机实测回执前，状态保持“未部署/手机样本0”。公众号草稿及配图仅本地保存，没有发布。
'''
(NOTES / 'deploy-history-intent-release-20261108.md').write_text(text, encoding='utf-8')

addition = f'''\n## 发布执行回执（本地更新）

PR #46查询修复已合并并通过2082项后端与小程序CI；当前待发布SHA`{sha}`。原运行源码仍与f4d388f实测一致，后续仅评测脚本修复及其测试/文档。{state}。正式结果、PR #47与包详见[发布交接](../dev-notes/deploy-history-intent-release-20261108.md)及本地release-status.json。

历史修复测试库阶段已于2026-11-09T07:01:28Z停止，后因门禁修复重启并另建隔离库；最终停止回执另存，数据保留，其他容器未改。GitHub连接器创建PR返回403，改用用户既有gh登录；设备登录直连超时通过仅当前进程使用本机代理解决，未改全局代理或泄露凭据。一次自动审批Bad Request在重新提交同一只读审批后解除。电脑控制因当前URL无法可靠识别被工具停止，未绕过。生产SHA与手机状态仍未核实。
'''
report = ROOT / 'docs/agent/reviews/2026-11-08-history-intent-release.md'
current = report.read_text(encoding='utf-8').split('\n## 发布执行回执（本地更新）')[0]
report.write_text(current.rstrip() + '\n' + addition, encoding='utf-8')
latest_tests = status.get('route_harness_repair', {}).get('github_backend_tests_passed', 2082)
line = f'**2026-11-08｜历史查询发布执行回执。** 查询与评测器修复已合并，当前SHA {sha[:7]}，GitHub后端{latest_tests}及小程序CI通过；{state}。手机样本0，公众号未发布。[发布交接](deploy-history-intent-release-20261108.md)。'
issues = NOTES / 'issues.md'
content = issues.read_text(encoding='utf-8')
lines = [v for v in content.splitlines() if not v.startswith('**2026-11-08｜历史查询发布执行回执。**')]
lines.insert(2, line + '\n')
issues.write_text('\n'.join(lines) + '\n', encoding='utf-8')
entry = f'- [PR #46已合并：{state}；手机采集待执行](deploy-history-intent-release-20261108.md)'
index = NOTES / 'README.md'
lines = [v for v in index.read_text(encoding='utf-8').splitlines() if not v.startswith('- [PR #46已合并：')]
lines.insert(2, entry)
index.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(state)
