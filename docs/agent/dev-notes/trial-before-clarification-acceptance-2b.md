# trial-before-clarification-acceptance-2b：单次真实配对验收

2026-10-18｜状态：已出分，停等用户。非 R157。

冻结 local-fix-2b 候选，在真实数据库夹具上完成一次 DeepSeek 端到端原题30＋holdout10，沿用任务、金标与既有校准。539份来源验收前后不变；本次应用/prompt/测试修改0、新增本地回归0、失败重跑0。复用本地37相关＋4核心＋2边界回放作为前置证据，不计作新增真实成绩。

**结果：原题24/30（80%），holdout5/10（50%）。** 对照 guard-fallback-acceptance 的27/30、5/10，分别为 **−3、持平**；原题3例恢复、6例回退。最终交付急症/就医主判断误判 **0**、实际器械/握持编造 **0**。连续达标仍 **0/2**，未达到连续两次原题≥26且holdout≥8及两红线为0的目标。

本批估算 **¥1.26536148≈¥1.27**（原题¥1.12351160＋holdout¥0.14184988）；累计 **¥16.16856952≈¥16.17/¥60**。按[官方峰时价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)与实际返回Token计算，非账单，低于本批¥5上限。

| 失败归类（主标签） | 原题 | holdout |
|---|---|---|
| trial_before_clarification | 2：SV01-r1、SV05-r2 | 1：HO06-r1 |
| guard_fallback | 1：SV02-r2 | 1：HO04-r1 |
| unnecessary_clarification | 1：WX35-r1 | 1：HO03-r1 |
| return_start_inconsistent | 1：WX34-r2 | 0 |
| repair_arithmetic_conflict | 1：SV04-r2 | 0 |
| evidence_and_delivery_omission | 0 | 1：HO02-r1 |
| intent_failure | 0 | 1：HO08-r1 |
| 合计失败实例 | 6 | 5 |

SV05-r2另有重复澄清，作次要标签、不重复计失败数。`trial_before_clarification`合计3次、`guard_fallback`2次；两者均未关闭。HO04从上轮未确认试练转为最终空阻断，不能算通过；HO06虽正确要求尽快就医、停止深蹲减重试练，仍在卧推方法未知时许可“只做不引起疼痛的部分”，且将补充做法延后，仍失败。HO06缺神经血供等急迫信号，故主判断0误判不代表临床建议完整。

40实例的全部44回合最终交付、工具来源和轨迹已复核；草稿不当作实际交付。122/122模型HTTP成功，89/89工具派发成功（含拒绝统计口径，本次拒绝0）；缺调用、意图失败和护栏阻断仍计业务失败，工具成功不等于任务完成。44回合用量审计通过，40业务快照未改变；dev/holdout容器均exit 0，两个测试库已停止。审阅者为实现Agent，非独立盲评；单次随机样本不能证明补丁因果效果，未测真实用户或完整/并发P99。

执行使用本目录`host.cjs live dev/holdout`各一次，随后`usage-audit dev/holdout`、停止测试库及`compile_results.py`；均未新增模型重跑。原始回复、工具轨迹、逐题引文/判分与结算见[证据目录](../../../artifacts/agent-dev-notes/trial-before-clarification-acceptance-2b-20261018/)，其中`review-notes.json`覆盖40实例，`result.json`为汇总。

1. **什么变得更可靠了？** 本轮没有证明业务可靠性提升；两红线保持0，但原题下降、holdout未改善。
2. **还差什么？** 未确认执行、空阻断及表中其余交付问题仍在，连续达标未前进。
3. **下一步？** 停等用户；不自动修补、不开第二批、不编号R157。
