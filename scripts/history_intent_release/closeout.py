"""Write the local, reviewable closeout from retained measurement evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
NOTES = ROOT / 'docs/agent/dev-notes'
SOURCE = ROOT / '.tmp/agent-history-intent-20261108'
audit = json.loads((BASE / 'audit.json').read_text(encoding='utf-8'))
assert audit['total_provider_calls'] == 184
assert audit['backend_tests']['tests'] == '2082'
assert audit['batches']['live-c2-extra']['outcomes'] == {'completed': 7}

report = '''# 历史描述与日期修订：本地验收及发布进度

2026-11-08（America/Los_Angeles；测试业务日2026-11-09 Asia/Shanghai）。最终候选修复“完成过的训练”被误判写入、无任务状态时误用空ID，以及有来源的日期范围修订被模型展开为错误日期。原16任务全部完成，扩展7任务全部完成；2082项后端测试通过。这里的完成只指登记的合成开发任务，手机体验与线上效果尚未测得。

本地测量代码：f4d388f078048e786ee97221b18ba6b4ba42892b；首候选137461d4e1b93309e2ed716254aa55e22dea7497。基于上一轮3d37905，累计补丁基于main/51feceb570b8d0d374948c5bf0558201958d1d4f。独立检出`.tmp/agent-history-intent-20261108`，分支`codex/agent-history-intent-20261108`，根目录旧应用未覆盖。发布文档提交与合并SHA另记，必须核对实测354份backend源码一致。

## 实际改动

| 文件 | 改动与限制 |
| --- | --- |
| agent_intent.py | 识别“完成过的训练，每场…”中的名词修饰；仍保留真正完成、标记、保存及混合写入的判断 |
| agent_intent_model.py / agent_task_state.py | 始终传入服务端任务快照，包括空快照；无任务可续接时说明new语义，不猜ID、不放宽未知ID校验；已有failed任务可按真实ID继续 |
| agent_query_reports.py / agent_intent_model.py | 复用既有来源及整句范围修订判断，对明确绝对日期、今天/昨天/前天、最近N天核对模型展开；冲突进入已有两次尝试，仍错误则理解失败，不执行错误读取 |

这轮新增50项回归（31项历史描述/任务状态，19项日期），加上上一轮51项共101项。累计PR还包括每场已存时长交付、相对日期只读修订及“不纳入”等状态排除解析。运行文件共6个；模型配置、数据库结构、队列和小程序没有改动。

日期校验只收紧既有路径，不授予读写权限。来源资格由is_calendar_query_revision决定，其已有状态筛选分支与成功查询审计分支并非完全相同；不能概括为所有支持范围都强制要求成功审计。端点编辑、周语义、复杂范围以及多数首次日期查询仍走原有流程，不宣称任意日历表达均已校验。

## 全部真实模型结果

| 候选 / 批次 | 任务完成 | Run / HTTP | 保守估算人民币 |
| --- | --- | ---: | ---: |
| 137461d / live-core | 16/16 | 22 / 68 | 0.438122 |
| 137461d / live-extra | 6/7，1部分 | 12 / 24 | 0.175398 |
| f4d388f / live-c2-core | 16/16 | 22 / 68 | 0.438196 |
| f4d388f / live-c2-extra | 7/7 | 12 / 24 | 0.175398 |

本轮合计184次HTTP，估算¥1.227114，包括失败候选、所有重试；低于用户扩展后的220次/¥3本地上限。费用依据应用缓存未命中保守费率，184条调用与184条持久账本逐任务对齐，用量均可核实，非供应商账单。正式GitHub发布门禁在计划中独立登记，结果与费用另列，不能用本地23题替代。

原16题与上轮文件逐字一致，串行/并发2各8题。扩展7题分为已知回归X01–X03、导入旧失败对话但无服务端任务的R01、预先固定的三种新措辞N01–N03。首候选分别2/3、1/1、3/3；最终候选分别3/3、1/1、3/3。最终N组已见过首候选模型输出，属于复测，不再算未见输出的新集。出题与复核均由实施者完成，非独立盲评。

首候选X03原文：“时间范围改成前天，只要已完成的日期与每次耗时。”业务日11月9日，模型却展开成11月8日，服务端按此查询12分钟；应为11月7日30分钟。该任务虽然Run completed，仍判部分完成。全部原响应、上下文和工具轨迹保留，没有重跑同一候选换分。

最终X03日期和30分钟正确，但模型第一次就答对，34个Run都只需一次意图尝试。这不能证明真实批次触发过新增纠错逻辑。机制证据来自原错误响应的离线回放：错误→正确需两次尝试；重复错误则理解失败，不能形成可执行成功。跨月、跨年、闰年、缺展开、错误来源与混合写入均有确定性回归。

全部46个任务的业务快照及确认落库已复核；各批遗留Run为0。核心任务确认前均无业务写入，确认后活动计划和餐次回读正确，四批核心旧提案确认补验共8次均409。R01仅导入两条旧失败对话作为夹具，不伪造成功审计，不计旧失败为新的调用或通过任务。

## 等待时间与尚缺指标

| 最终原题组别 | 有效单轮数 / 总轮数 | 可见终态等待P50 | 样本P95 | 排队P50 |
| --- | ---: | ---: | ---: | ---: |
| 串行 | 9 / 11 | 4.86秒 | 6.48秒 | 0.25秒 |
| 并发2 | 9 / 11 | 7.28秒 | 9.71秒 | 2.05秒 |

测量为本机TCP、单worker、真实模型、合成数据库，从提交到采集器读到完整终态；含轮询，不包含手机网络、微信渲染或用户阅读时间。P95采用nearest-rank，仅描述小样本，不是生产SLO。四批分别3、1、4、1个单轮记录超过既定50ms双时钟偏差阈值，保留原值并设为未知，不当作0，也不据此宣称性能提升。最终原题任务活动时间P50串行4.88秒(n=6)、并发2为7.29秒(n=6)，不含用户思考。

真人反馈n=0，满意度/主动纠正率/手机端等待仍为空。未覆盖长会话、大量历史数据、多worker容量、生产重启及所有自然语言日期。历史报告仍有组次/容量摘要，未按“仅指定列”严格单独评分。合成场次没有SessionExercise，0组是夹具事实，已存30/12分钟不是推断。S01只复核原合同且回复逐字一致，本轮不构成新增临床有效性验证。

## 确定性验证与所有失败

- 新增31项：修改前10失败/21通过，修复后相关181通过；新增19项日期：修改前19失败，其中2项原错误路由被接受、17项新函数尚不存在，不等于19个独立产品缺陷；修复后相关200通过。
- 首候选首次全量1963通过、100个setup错误：专用查询夹具拒绝非回环数据库主机名。改测试容器网络为共享专用数据库网络、连接127.0.0.1，未修改夹具或产品，保留失败后完整重跑得到2063通过。
- 最终后端2082通过、868警告，0失败/0错误/0跳过；0032升级→0023降级→head升级、39项意图规则、15项脚本任务、多轮与提案修订检查等8步全部通过。对应checks-c2/checks.json及日志。
- 只读复核最初使用了错误的tool_name键，改为实际tool；人工评审文件最初缺evidence定位字段，补实际路径后审计通过，任务判分没有改变。宿主机python未在PATH，改用已有.venv解释器；均为采集/工具问题，非模型失败。
- 354份最终backend源码在真实运行前后及收尾审计一致；凭据不写入公开材料。原始响应仅合成用户数据，公开草稿和图均使用这批合成内容。

## 复核入口及发布状态

- [修改前计划与轮次记录](../dev-notes/history-intent-release-20261108.md)
- [审计汇总](../../../artifacts/agent-dev-notes/history-intent-release-20261108/audit.json)
- [首候选扩展全部原文](../../../artifacts/agent-dev-notes/history-intent-release-20261108/live-extra/result.json)
- [最终核心原文和工具轨迹](../../../artifacts/agent-dev-notes/history-intent-release-20261108/live-c2-core/result.json)
- [最终扩展原文和工具轨迹](../../../artifacts/agent-dev-notes/history-intent-release-20261108/live-c2-extra/result.json)
- 其余：同目录before/、before-manifest.json、original-x01.json、original-x03-date.json、四批reviews.json、persistence-verification.json及checks-c2/。可运行`scripts/history_intent_release/audit_results.py`重新核算，不调用模型。
- [公众号本地草稿](../dev-notes/history-intent-release-20261108-wechat-draft.md)、[指标示意图](../dev-notes/assets/history-intent-release-20261108/metrics.png)、[手机采集单](../dev-notes/history-intent-release-20261108-phone-checklist.md)。

本报告提交时：本地修复与完整复测完成；GitHub PR/CI/正式门禁、部署及手机采集尚未完成，执行回执另存release-status.json并在本地报告追加。不把准备好的包或脚本算作已发布。公众号仅本地素材。
'''
# Fix an ambiguous phrase: there are two core batches, four writes per core batch.
report = report.replace('四批核心旧提案确认补验', '两批核心旧提案确认补验')
(ROOT / 'docs/agent/reviews/2026-11-08-history-intent-release.md').write_text(report, encoding='utf-8')

closeout = '''
## 最终本地结果（覆盖前文待执行状态）

最终代码f4d388f：原16/16、扩展7/7；首候选137461d原16/16、扩展6/7，X03“前天→昨天”失败完整保留。两候选合计184 HTTP/估算¥1.227114，最终全部2082项后端测试通过。费用上限以用户后续批准的220次/¥3为准，前文120次是修改前记录。最终354份backend源码一致；全部46任务、68 Run、184条账本核对、确认落库和过期提案拒绝补验完成。

最终扩展中已知回归3/3、空任务恢复1/1、三种新措辞复测3/3；它们已用于开发，非独立盲测。真实最终批次日期首次即正确，新增防错机制由旧错误响应回放验证；不夸大为真实触发。日期校验复用既有来源资格，含现存状态筛选分支，不概括为全部都要求成功审计。

最终原题有效单轮n=18/22，串行P50/P95 4.86/6.48秒，并发2为7.28/9.71秒；小样本本地TCP终态等待，非手机指标。四批总9条计时异常按预登记规则排除，真人反馈n=0。详细所有失败、测试命令、范围及发布状态见[最终验收](../reviews/2026-11-08-history-intent-release.md)。

已准备[公众号草稿](history-intent-release-20261108-wechat-draft.md)及assets/history-intent-release-20261108中的source.json、可复现渲染脚本和指标示意图；完整原文与SHA256可追溯，图不是小程序截图。仅本地保存。已准备手机端采集单，未填实测值。下一步按既有授权推进PR、正式发布门禁、同SHA打包部署，再采集实机。
'''
p = NOTES / 'history-intent-release-20261108.md'
content = p.read_text(encoding='utf-8')
content = content.split('\n## 最终本地结果（覆盖前文待执行状态）')[0]
content = content.replace('问题：QUERY-20261108-02。状态：修改前计划；', '问题：QUERY-20261108-02/03。状态：本地修复与复测完成，发布推进中；以下保留修改前计划；')
p.write_text(content.rstrip() + '\n' + closeout, encoding='utf-8')

status = '**2026-11-08｜QUERY-20261108-02/03本地完成，发布推进中。** 最终f4d388f原16/16、扩展7/7，2082项后端通过；首候选137461d扩展6/7的日期错误保留。两候选184次HTTP/估算¥1.227114（上限220/¥3），354份源码一致，原文/工具/账本与落库核对完成。最终三种新措辞属于复测，非盲测；真人反馈与手机指标仍为空。GitHub PR、正式门禁与部署状态另记，公众号草稿和示意图仅本地保存。[过程](history-intent-release-20261108.md) / [验收](../reviews/2026-11-08-history-intent-release.md)。'
entry = '- [历史描述与日期修订：最终原16/16、扩展7/7，2082项回归；184次HTTP约1.23元，发布推进中](history-intent-release-20261108.md)'
for target in (NOTES, SOURCE / 'docs/agent/dev-notes'):
    for name, line in [('issues.md', status), ('README.md', entry)]:
        q = target / name
        value = q.read_text(encoding='utf-8')
        if line not in value:
            first, rest = value.split('\n', 1)
            q.write_text(first + '\n\n' + line + '\n' + rest, encoding='utf-8')

draft = '''# 开发笔记：答得很像对的，日期也可能错一天

这轮先补一个历史查询问题。用户问“过去一周完成过的训练，每场日期和用了多少分钟”，模型已经识别为查询，本地规则却把“完成”当成写入命令。我们收窄了这个词在名词修饰中的判断，并把空任务状态明确交给模型，避免失败对话之后凭空续接一个不存在的任务。

第一候选的原16项任务全部完成，扩展测试却出现了另一个问题。业务日期是11月9日，用户把范围改成“前天”，模型展开成11月8日。后端照着这个日期，交付了12分钟的真实记录。回答很完整，查到的数据也是真的，但查错了那一天；正确结果应是11月7日、30分钟。因此这项只能记部分完成。

随后增加的是日期一致性检查：在已有来源的整段日期范围修订中，把明确日期或支持的相对天数与模型展开核对。发现冲突就使用已有的有限修复机会；反复出错则停止这次理解，不执行错误读取。这不是把所有日历表达都做成规则，周范围、复杂表达和多数首次查询仍有未覆盖部分。

最终候选的原16项与扩展7项都完成，后端2082项测试通过。整个过程184次模型HTTP，包含第一候选的错误，按应用保守费率估算约1.23元。原响应、工具轨迹、修改前代码和失败日志都有留档。

有一个容易夸大的地方：最终真实测试中，模型第一次就把日期算对了，所以这批成功不能证明新增纠错逻辑实际触发过。我们另用旧错误响应回放，确认错一次会修复，连续错误会被拒绝。真实任务结果和机制验证分开记录。

![本地合成任务的真实模型指标示意，不是小程序截图](assets/history-intent-release-20261108/metrics.png)

原题单轮终态等待P50，串行约4.86秒，并发2约7.28秒。这是本机TCP与单worker的小样本，含轮询，没有手机网络和微信渲染，不能当作上线体验承诺。出题和复核也是同一实施者，三种新措辞在最终候选中已经属于复测，不称盲测。

接下来要通过独立的正式发布门禁，部署同一份代码，再在手机上记录真实等待、纠正次数和任务完成情况。稿件此时仅本地准备，发布与手机数据尚未补齐。

素材来源：history-intent-release-20261108轮次记录、最终验收报告、assets/history-intent-release-20261108/source.json及其文件指纹。所有公开对话均为合成数据，配图为指标示意。
'''
(NOTES / 'history-intent-release-20261108-wechat-draft.md').write_text(draft, encoding='utf-8')

phone = '''# 同SHA上线后的手机体验采集单

状态：准备完成，未执行。只在正式发布门禁通过、目标SHA上线且旧worker退出后测量。不要把本地API、模拟器、开发工具或回放的时间填进手机列。

## 采集前

记录部署号、完整build_commit、手机型号、微信版本、系统版本、网络(Wi-Fi/蜂窝)、开始时间及时区。核对当前业务日为Asia/Shanghai。用测试账号或本人明确同意的记录；分享材料移除姓名、标识与私人健康信息。没有历史场次时如实记录，不为“通过”制造线上记录。

## 六个任务（首轮每项一次，所有失败都保留）

1. 查询当前计划：“我当前的训练计划是什么？”核对确为当前启用计划。
2. 历史列表：“请列过去一周完成过的训练，每场日期和用了多少分钟。”核对每条真实记录及未记录时长的提示。
3. 承接上题：“范围收窄到最近两天，仍只要已完成的日期和用时。”核对两天范围、只读和每场用时。
4. 再追问：“时间范围改成前天，只要已完成的日期与每次耗时。”按当时上海业务日期核对，不固定使用11月7日。
5. 只读建议：“结合我已有的计划，给下一次训练的准备建议，先不要修改。”记录是否读到正确计划、是否提前写入，不要求执行任何动作。
6. 在新会话问一个已知可核对的饮食记录查询；无饮食记录时接受明确无记录，不判为系统失败。原文随采集保存。

每项记录发送到第一条可见状态、发送到完整回复/提案的手机实测秒数，最好录屏后按同一视频时间轴读数。停止计时点为内容完整可读，不能只以loading消失或服务器completed代替。另记是否完成、是否纠正、纠正原文与次数、是否出现无关提案或非授权写入、本人满意度1–5。不要填写估计时间；无法测得就留空并写原因。

模板列：sample_id, build_commit, deployment_id, device, wechat_version, network, business_date, task, sent_at, first_visible_seconds, complete_visible_seconds, completion, corrections, satisfaction_1_to_5, run_id, evidence_path, notes。

成本由该账号实际预算账本前后差或供应商可核对用量另记；手机感知耗时与后端耗时分列。六项是首轮观察，不够估计生产P95或稳定成功率。连续复测需另行登记，不删除失败。

## 发布状态

尚无手机样本，所有真实体验字段为空。部署和正式门禁回执在本地release-status.json与发布说明中持续补记。
'''
(NOTES / 'history-intent-release-20261108-phone-checklist.md').write_text(phone, encoding='utf-8')
(BASE / 'phone-observations-template.csv').write_text('sample_id,build_commit,deployment_id,device,wechat_version,network,business_date,task,sent_at,first_visible_seconds,complete_visible_seconds,completion,corrections,satisfaction_1_to_5,run_id,evidence_path,notes\n', encoding='utf-8-sig')

for name in ('history-intent-release-20261108.md', 'history-intent-release-20261108-wechat-draft.md', 'history-intent-release-20261108-phone-checklist.md'):
    (SOURCE / 'docs/agent/dev-notes' / name).write_bytes((NOTES / name).read_bytes())
# Raw traces stay local; public report retains explicit relative artifact locators.
import re
public_report = re.sub(r'\[([^\]]+)\]\((\.\./\.\./\.\./artifacts/[^)]+)\)', r'\1（本地路径：`\2`）', report)
(SOURCE / 'docs/agent/reviews/2026-11-08-history-intent-release.md').write_text(public_report, encoding='utf-8')
print('Wrote local report, notes, draft, phone checklist and public documentation copy; no model calls.')
