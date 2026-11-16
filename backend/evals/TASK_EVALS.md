# 完整任务契约验收

此门禁检查真实 PostgreSQL、Agent Runtime、API、提案状态和数据库回读。它补充已有规则、Controller、查询及模型评测，不用一个 Judge 分数代替业务断言。

## 范围与口径

- 版本化矩阵：`agent_task_cases.json`。初版15题，1.1版为开发集10题、保留集5题。
- 场景：生成—保存—确认—重复确认—回读，拒绝、过期、资料变化、修订旧方案失效、非法模型输出、跨午夜恢复、稀疏饮食记录、五/六类证据、低预算、缺失观察。
- 七个维度：语义、证据、事实、任务状态、持久化、展示数据、耗时。不存在的维度为 not_applicable；必测事实缺失为 not_run 并阻断。
- scripted 模式仅替换模型边界；真实工具、SQL约束、运行器、API均执行。语义层始终标记 not_run，不能把它当作真实模型成功率。
- live 模式调用已配置模型。目前支持 meal_confirm、query_today、query_sparse_history。模型原文、结构化输出、API响应、工具审计和数据库回读均保留，账户与食品均为合成数据。
- 拒绝、过期、澄清等案例的“通过”表示符合预期处理，不能等同于用户业务完成率。
- presentation 是 API 卡片与正文契约；没有覆盖微信渲染、触控、设备网络。耗时包含本地任务步骤，不是生产 p95；Run token 计数可能漏掉结构化子调用，不据此声称成本降幅。

## 执行

使用本地专用的 `*_test` PostgreSQL，安装 vector 扩展；可复用 CI 的 pgvector 测试服务。评测每题建立独立随机 schema，结束后只删除该 schema。拒绝远程地址、非测试库名和连接选项。

```powershell
$env:AGENT_QUERY_EVAL_DATABASE_URL='postgresql+asyncpg://fitness:fitness_pass@127.0.0.1:5432/fitness_test'
$env:DATABASE_URL=$env:AGENT_QUERY_EVAL_DATABASE_URL
$env:SECRET_KEY='test-only-eval-secret'
$env:PYTHONPATH='backend'
python backend/scripts/evaluate_agent_tasks.py --split development --output artifacts/tasks-dev-001
python backend/scripts/evaluate_agent_tasks.py --split holdout --output artifacts/tasks-holdout-001
python backend/scripts/evaluate_agent_tasks.py --mode live --split all --cases meal_confirm,query_today,query_sparse_history --output artifacts/tasks-live-001
```

真实模式从正常环境变量读取模型设置，禁止把密钥写入报告。输出目录必须是新的，已有结果不能覆盖。返回码非0即阻断；必须同时检查 pytest 返回码与逐题判定，运行中断、无记录和夹具错误均不能算通过。

## 失败回流与复算

每次输出包括 `report.json`、`failure-inbox.json`、`pytest.log` 与逐题原始 JSON。失败初始分类为 untriaged，排查后在开发笔记中区分模型错误、应用错误、夹具问题、评测器误判；保留原报告，不能删除重试分母。

```powershell
python backend/scripts/evaluate_agent_tasks.py --mode live --split all --cases meal_confirm,query_today,query_sparse_history --replay-from artifacts/tasks-live-001 --output artifacts/tasks-live-001-rescored
```

复算不会访问数据库或模型；会从原始 API 正文与卡片重新计算新增的范围检查。修复后再执行一轮，不能把重算当成实跑。

round07 的 query_sparse_history 在真实模型验收中暴露“近30天”与8月1日/9月29日矛盾。1.0误判为通过，1.1新增正文检查并将这题移入开发集；已经用于修复的样本不能继续宣称保留集或盲测。后续优先把脱敏真实失败追加到开发集，另补未接触过的保留样本，不用大量同义句夸大独立覆盖。
