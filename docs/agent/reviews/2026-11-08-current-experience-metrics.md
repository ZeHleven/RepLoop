# 0.5.47 当前主线：真实模型体验基线

2026-11-08（America/Los_Angeles）；业务日期 2026-11-09（Asia/Shanghai）。本地测量与复核已完成，产品代码未修改，未部署。

本批 8 种合成任务各测串行、并发2一次：**16 个任务实例中 14 个完整完成、2 个部分完成**。22 个 Run 全部标记 completed，却有两个完整目标未交付。基础资料、当前计划、饮食多轮、确认后写入在本批符合预登记要求；训练历史的时长交付与日期追问均有稳定复现的问题。

这是一批小样本、真实模型的本地功能和等待基线。不能将 87.5% 称为线上成功率、真人满意度或独立盲测成绩。

## 版本与环境

| 项目 | 实际值 |
| --- | --- |
| 用户回报的线上版本 | 0.5.47 / e871b4d4b97c，未在本轮测量 |
| 本轮测量源码 | main / 51feceb570b8d0d374948c5bf0558201958d1d4f，干净独立检出 |
| 模型 | 请求 deepseek-v4-flash；供应商响应 model 为 deepseek-flash |
| 模型密钥 | 经用户确认使用本机 .env 中现有模型密钥；数据库与其他生产配置未导入 |
| 数据 | 每任务独立合成用户、会话、计划、训练记录与饮食；无真人数据 |
| 服务 | 实际 loopback TCP → FastAPI → PostgreSQL 队列 → 原生产 worker → 真实供应商 HTTP |
| 资源 | 1 API进程、1 worker，2 CPU / 2 GiB，主机其他负载未控制 |
| 运行依赖 | Python 3.13.15；复用 fitness-agent-backend-ci:0.5.36-local 的依赖，当前源码只读挂载；实际版本清单见 freeze.json |
| 开关 | 症状范围、症状事实提取、异步worker开启；五类提案开关显式开启；完整配置已冻结 |
| 数据库 | 独立 PostgreSQL，Base.metadata.create_all 建表；本轮没有验证迁移链 |
| 观察开销 | worker增加一次归属查询，模型HTTP增加记录；未做开销扣除或优化因果推断 |

串行与并发组按顺序运行，非随机对照；只测两名客户端，不代表系统最大承载能力。样本由实施者编写和复核，未安排独立评审。没有为了提高成绩修改任务、提示词或产品后再重跑。

## 等待和费用

下表的单轮等待从调用端提交前计到轮询取得终态，轮询默认800ms，范围500–2000ms。包含排队、处理、轮询等待，**不含手机公网、微信转发和页面渲染**。P50/P95为最近秩样本值，小样本P95等于本组最大值，不能用作生产SLO。

| 指标 | 串行 | 并发2 |
| --- | ---: | ---: |
| 任务完整完成 | 7/8（87.5%） | 7/8（87.5%） |
| 部分完成 | 1 | 1 |
| 单轮等待有效样本 | 9/11 | 9/11 |
| 单轮等待 P50 / P95 | 4.85 / 5.66 秒 | 6.47 / 9.70 秒 |
| 排队 P50 / P95 | 0.33 / 0.50 秒 | 1.89 / 4.64 秒 |
| 处理 P50 / P95 | 4.11 / 4.58 秒 | 3.92 / 5.10 秒 |
| 完整任务主动耗时 P50 / P95（含部分完成目标） | 4.87 / 10.51 秒，n=6 | 6.47 / 12.97 秒，n=6 |
| 仅成功任务主动耗时 P50 / P95 | 4.87 / 10.51 秒，n=5 | 6.47 / 12.97 秒，n=6 |
| 模型 HTTP 尝试 / 可核实用量 | 35/35 | 35/35 |
| 输入 / 输出 Token | 81,828 / 7,772 | 82,063 / 7,850 |
| 应用保守估算费用 | ¥0.225832 | ¥0.226926 |
| 每成功任务摊销费用（含部分完成任务成本） | ¥0.032262 | ¥0.032418 |

本批合计70次模型HTTP、163,891输入Token、15,622输出Token，估算 **¥0.452758**；按14个成功任务摊销约 **¥0.03234/个**。70条观察记录与70条持久费用账本逐任务对齐，未用Run上的局部Token冒充完整调用成本。费率为应用冻结的 `deepseek-2026-10-22-peak-cache-miss`，按缓存未命中保守计，**不是供应商实时报价或实际账单**。

四条单轮计时的单调钟与墙钟差异约1.44–1.55秒，按预先约定的50ms阈值，将该轮的可见/排队/处理值及所属任务主动耗时设为未知，不删除语义结果：serial-Q02第1轮、serial-W01第2轮、concurrent_2-Q03第2轮、concurrent_2-Q04第2轮。原始时钟值在 result.json 和 audit.json 保留。共18/22单轮、12/16任务有有效时间；这种缺失也可能影响分位数的代表性。具体时钟异常来源尚未定位。任务耗时为各轮和首次确认之和，排除用户思考、夹具准备和导出；确认HTTP只记录了单调钟，未独立校验墙钟。

## 逐任务结果

| 冻结任务 | 串行 / 并发2 | 主要验收证据 |
| --- | --- | --- |
| Q01 资料查询 | 完成 / 完成 | 身高170cm、体重65kg、保持体重、每周3天，与 profile.get_summary 一致 |
| Q02 当前计划 | 完成 / 完成 | 卧推3×10/20kg、深蹲3×10/30kg，与 plan.get_active 一致 |
| Q03 已完成训练+日期修订 | 部分 / 部分 | 日期正确但漏30/12分钟；追问最近3天被规则误拦 |
| Q04 昨天/前天晚餐 | 完成 / 完成 | 两轮食品、克数、日期、四项总量均正确；明确是已记录饮食 |
| W01 计划修订并确认 | 完成 / 完成 | 确认前未改，确认后唯一活动计划卧推5×8/20kg，深蹲不变；旧提案stale |
| W02 记餐并确认 | 完成 / 完成 | 确认前未写，确认后恰好一餐，150g米饭+100g鸡胸肉，营养值正确 |
| S01 当前肩部不适取舍 | 产品契约完成 / 完成 | 确定性路径、暂停卧推、深蹲附条件、不新增动作、不改计划；非临床有效性评价 |
| G01 两点热身知识 | 产品契约完成 / 完成 | 两点回应、不查个人记录、不作个人健康保证；未独立核验医学事实 |

W01的原始业务快照包含历史和活动计划，不能把四行动作误判为重复写入。补充数据库核验显式读取 is_active，确认每个用户只有一个活动计划。旧提案分别以旧版本号和当前版本号尝试确认，共4次409，业务快照未变。该补验经ASGI调用、禁止外部HTTP，不计入真实模型请求或性能样本。

## 两个产品缺口及归因

1. **训练历史漏交付时长，两组均复现。** 数据库有已完成场次2026-11-07/30分钟、11-08/12分钟，另有当天进行中场次。回答列对日期和状态，却交付了组数/次数/容量，没有用户要求的时长。实际走 `verified_calendar_history_report`；冻结源码的 `render_calendar_history` 没有渲染时长。属于产品查询/交付链缺口，不应笼统归因于模型幻觉。夹具没有动作组记录，输出0组/0次属稀疏夹具事实，本轮不据此报缺陷。
2. **只读日期修订被写入保护规则误拦，两组均复现。** 输入“改成最近3天，仍只要已完成的日期和每场时长。”原始HTTP中，模型首次及重试都返回 `workout_history / query / read` 和正确日期；后端返回 `semantic_mutation_structure_missing`，最终提示暂时无法处理，没有查到数据。证据为 serial http-11/13、concurrent_2 http-50/52；校验位置见冻结的 agent_intent_model.py。归因是查询修订与规则校验的兼容问题，未做单行根因修复实验。

两次问题是同一题跨两组复现，不能说成两类独立泛化失败率。这轮只登记问题，没有改动Agent行为。

## 全部尝试与证据边界

- offline-01：4个采集器任务、12次注入503，12/12归属一致，未访问真实模型；不计正常能力、延迟或实际费用。
- live-01：按原计划取本机 .env.production，首任务两次并行请求均401，停止新请求。1任务开始、15未执行，费用用量未知，保留上界占额¥0.197056。不能把鉴权快速失败当低延迟成绩，也不能记成零费用。这不证明云上实际密钥失效。
- live-02：用户明确改用 .env 后，新建独立数据库，同SHA、同题、同判断标准完成16任务；没有产品修补。成功批估算费用与前批未知占额合计 **¥0.649814/¥5**，真实HTTP共72次，实际总账单未知。初批没有混入有效基线，也没有被删除。
- 单元检查13/13；产物核验70/70模型调用归属、70/70账本一致、0排队遗留、347源码文件前后指纹一致；本轮只创建的数据库容器已停止，数据保留。
- 未测：用户当前线上e871b4d、手机网络/渲染、真人纠正/重复说明/明确放弃/满意度、长会话和大数据、超过2客户端的并发容量、冷启动、正式发布门禁。无真人样本时指标是0条/null，不是0次纠错或100%满意。

## 复核入口

- [冻结协议](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/PROTOCOL.md)、[预登记任务](../../../scripts/agent_experience/tasks.json)、[逐任务复核](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/reviews.json)
- [汇总指标](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/metrics.json)、[审计与总预算](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/audit.json)
- [原始回复/工具轨迹/业务快照](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02/result.json)、[逐HTTP日志](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02/provider-events.jsonl)、[写入补验](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02/persistence-verification.json)
- [原始证据](../../../artifacts/agent-dev-notes/current-experience-metrics-20261108/)、[采集与真人反馈说明](../../../scripts/agent_experience/README.md)

只读复算命令：

```powershell
.venv/Scripts/python.exe scripts/agent_experience/metrics.py artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02/result.json --reviews artifacts/agent-dev-notes/current-experience-metrics-20261108/reviews.json --feedback artifacts/agent-dev-notes/current-experience-metrics-20261108/feedback.json --out artifacts/agent-dev-notes/current-experience-metrics-20261108/metrics.json
.venv/Scripts/python.exe scripts/agent_experience/audit_artifacts.py
```

要补线上真实体验，下一份记录必须先确认实际后端SHA。真人每个完整目标记录结果、纠正次数、重复说明次数、等待感受和满意度；模板已准备，不能用这16个合成任务代填。
