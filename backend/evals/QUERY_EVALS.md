# 查询任务评测

`judge_eval.py` 使用当前意图模型与实际只读工具的名称、Schema 和描述，替换业务 I/O 为合成数据快照；根据当前 trace 路由选择普通执行或规划执行。它不测试队列、数据库或小程序 UI。

`agent_judge_cases.json` 为早期通用样例；本轮验收使用 `artifacts/agent-dev-notes/round-02/evidence/cases.json` 的 12 个查询任务。旧 pending 夹具已补齐当前主分支保存的语义和 evidence_requirements，未改变用户原题和训练记录。

评分同时检查工具证据、事实约束及 LLM Judge 的 grounding/completion；高总分不能掩盖这两项的不完整。Judge 未经独立人工校准，不能视为准确率测量。

`sqlite_test_fixtures.py` 必须显式 `--noconftest -p evals.sqlite_test_fixtures` 使用。只建内存 SQLite 副本，过滤 PostgreSQL 专用约束，并保留适用的简单约束/部分索引。不得把此验证称作 PostgreSQL 提案一致性验证。

依赖及完整命令见 [主分支迁移记录](../../docs/agent/21-query-reliability-migration.md)。
