# 查询可靠性修复的当前主分支迁移

本次基于 `2fa5bf7702128e29cd81c446c8c62d73e948ae90`，增量迁移原查询修复，保留 SemanticRouteV2、规划执行、提案授权和工具注册表。

## 行为变化

- 上次成功统计运行可以支持纯时间追问。上下文来自同用户/同会话的运行与成功工具审计，不从任意历史文本授予权限，不再复制查询文本进消息。
- 明确新任务会丢弃旧只读澄清；模型不可用时新任务停止，旧 pending 不再意外授权另一个查询。写入澄清仍交给现有结构化模型。
- “完成组数”“保存的训练目标”是统计名词/已有资料，不应被误判为写命令；真正的完成训练和复合修改请求仍按写操作处理。
- 范围明确的日历周汇总和最近训练比较，通过原注册工具读取实际数据并生成事实报告。保留审计、卡片、trace、注册表；程序消息不计为模型调用。开放建议及多目标保留原执行链路。
- 统计排除未来记录，提供完整周、周日和日历周平均；保留提前结束训练、selected_week、daily。动作重量基于实际完成组。

不新增数据库列，不改变生产数据库约束。不执行历史 query_context 清理。

## 验证

最终 419 项测试通过，覆盖意图/规划/trace/工具注册表及 API 行为一致性，包含 9 项新增 SQLite SQL/持久化测试。SQLite 使用测试表副本，跳过 PostgreSQL 特有 JSON、正则与 interval 约束，不能证明 PostgreSQL 锁、并发、迁移或写提案约束。

固定 12 任务以真实模型、合成工具快照测试。第一轮旧门槛 6/12；修复后第二轮原门槛 11/12，余下 T06 是评测器混淆训练场次和重复次数。修复检查器后，对保存的同一批回答重新检查为 12/12；T06 另跑两次均通过。重算不是新增模型试验，样本不能代表总体准确率。

第二轮之后仅对可信时间追问增加合法周数范围检查，最终边界/持久化测试覆盖；未把整批第二轮冒称该字节版本的实测。

## 可复现命令

安装 `backend/requirements-evals.txt`，设置仅用于测试的 DATABASE_URL、SECRET_KEY，清空 DEEPSEEK_API_KEY。运行单测时不要加载会连接配置 PostgreSQL 的公共 conftest：

```sh
python -m pytest backend/evals/tests backend/tests/test_agent_intent.py backend/tests/test_agent_intent_model.py backend/tests/test_agent_controller.py backend/tests/test_agent_trace.py backend/tests/test_agent_tool_registry*.py backend/tests/test_workout_queries.py backend/tests/test_agent_tools.py backend/tests/test_agent_runtime_v1.py --noconftest -p evals.sqlite_test_fixtures -p no:cacheprovider -q
```

部分 Shell 不展开通配符，需显式列出 test_agent_tool_registry*.py 文件。可另用以下命令跑新增回归：

```sh
python -m pytest backend/evals/tests --noconftest -p evals.sqlite_test_fixtures -p no:cacheprovider -q
python backend/scripts/evaluate_agent_judge.py --case-file artifacts/agent-dev-notes/round-02/evidence/cases.json --validate-only
```

真实模型测试需要配置模型凭据并使用新输出目录：

```sh
python backend/scripts/evaluate_agent_judge.py --case-file artifacts/agent-dev-notes/round-02/evidence/cases.json --output artifacts/query-live-new --repeat 1 --strict
```

原始前后报告、失败尝试、代码快照/指纹和迁移脚本另随本地 `round-04-main-migration` 证据包保留；公众号补充稿及示意配图单独打包，未发布。尚未提交、推送或部署，PostgreSQL 集成、小程序现场和完整空间基准待后续验证。
