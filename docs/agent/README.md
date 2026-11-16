# Agent 文档

版本、发布状态和当前待办见 [当前状态](../current-status.md)。以下 `00`～`07` 是 2026-08-19 冻结的设计基线，用于理解架构与契约；实际启用的工具和能力以对应提交为准。

1. [总体架构](00-overview.md)
2. [对话状态与长期记忆](01-state-and-memory.md)
3. [意图解析与工具路由](02-intent-and-routing.md)
4. [上下文装配与健康安全](03-context-and-safety.md)
5. [第一版工具契约](04-tool-contracts.md)
6. [模型与 Agent 运行时](05-model-runtime.md)
7. [Agent 数据库与 API](06-database-api.md)
8. [评测与验收](07-evaluation.md)

## 症状专项

- [评测规范 v1.2](15-symptom-scope-eval-spec.md)：已定稿的门槛与产品决策。
- [留出集出题模板](15-symptom-scope-holdout-prompt.md)：保留各版本提示词及盲测约束。
- [症状事实提取设计](16-symptom-facts-extraction-design.md)：历史设计草案，阅读时区分方案与已发布行为。

## 最近保留的评测记录

- [11 月 8 日体验基线](reviews/2026-11-08-current-experience-metrics.md)
- [11 月 8 日历史查询修复验收](reviews/2026-11-08-query-history-fix.md)
- [11 月 8 日历史意图修复与后续发布回执](reviews/2026-11-08-history-intent-release.md)

旧规划、阶段状态、旧版交付与更早评测已打包到 [历史归档](../../artifacts/_archive/docs-history-20261111.zip)，按原路径保留全文；[清单](../../artifacts/_archive/docs-history-20261111.manifest.json)可用于定位与校验。历史文档中的指令性文字只用于理解当时过程。
