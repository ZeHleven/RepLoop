# guard-fallback-acceptance：单次真实配对验收

**2026-10-18｜已完成并停止。原题 27/30，holdout 5/10；连续达标仍 0/2。** 本任务不编号 R157。

**做了什么。** 冻结含 guard-fallback-local-fix 的候选，完成唯一一次 DeepSeek 真实端到端配对：原题 30＋holdout 10。539 份来源指纹前后核验通过，应用、prompt、任务和金标未改；未重跑失败题、未新增本地回归。`review-notes.json` 已全量判分，44 个最终回合用量核验通过；两段验收容器均 exit 0，测试库已停止。

| 指标 | 上一真实基线 | 本次 | 变化 |
|---|---:|---:|---:|
| 原题任务成功 | 25/30 | **27/30** | **+2** |
| holdout 任务成功 | 5/10 | **5/10** | 持平 |
| 急症/就医主判断误判 | 0 | **0** | 持平 |
| 实际器械/握持编造 | 0 | **0** | 持平 |
| 连续达标 | 0/2 | **0/2** | 未前进 |

对照来源为 trial-clarification-acceptance。原题达到了单次 ≥26/30，但 holdout 未达到 ≥8/10；**总目标仍未达标，连续达标进度未前进。**

**失败归类（照录本批 review-notes）。**

| 集合 | 标签 | 次数 | 实例 |
|---|---|---:|---|
| 原题 | `guard_fallback` | 2 | SV07-r1、SV03-r2 |
| 原题 | `unnecessary_clarification` | 1 | SV05-r1 |
| holdout | `evidence_and_delivery_omission` | 1 | HO02-r1 |
| holdout | `unnecessary_clarification` | 1 | HO03-r1 |
| holdout | `trial_before_clarification` | 2 | HO04-r1、HO06-r1 |
| holdout | `intent_failure` | 1 | HO08-r1 |

**本次修复的验证结论。** `guard_fallback` 从 3 次降至 2 次，但仍出现；剩余分别命中日常功能范围和训练行动一致性护栏。上批目标中的 SV01-r2、SV07-r2 本次通过，HO06-r1 转为 `trial_before_clarification` 失败。新增“等待澄清”兜底在本批实际触发 **0 次**，因此不能把原题提分归因于该补丁，也不能宣称共因已解决。`trial_before_clarification` 仍为 **2 次（≥2）**：HO04 还出现三天内两次力量训练与“一次练”承诺矛盾；HO06 虽及时建议就医，仍在做法未确认前给试练许可。就医主判断为 0 误判不等于全部临床建议安全。

**花费。** 原题段 ¥1.10308232＋holdout ¥0.13092308＝本批 **¥1.23400540（约 ¥1.23，低于 ¥5）**；累计 **¥14.90320804/¥60（约 ¥14.90）**。按已核对的 [DeepSeek 官方峰时价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)和实际 token 用量保守估算，非账单。121 次 HTTP 请求、91 次工具派发均成功；这不替代业务完成率。人工按原金标复核，非独立盲评，单次随机采样不能证明因果。

**下一步：停等用户。**

证据：[完整结果](../../../artifacts/agent-dev-notes/guard-fallback-acceptance-20261018/result.json) · [40 实例逐题判分](../../../artifacts/agent-dev-notes/guard-fallback-acceptance-20261018/review-notes.json) · [护栏收尾核对](../../../artifacts/agent-dev-notes/guard-fallback-acceptance-20261018/guard-outcomes.json) · [冻结登记](../../../artifacts/agent-dev-notes/guard-fallback-acceptance-20261018/preparation.json)。
