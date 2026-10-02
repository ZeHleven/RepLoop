# 云托管部署包

当前整合候选版本为 `0.5.46`，数据库目标为 Alembic `0032`。本地交付与手机验收完成的范围不等于生产发布通过；正式产物必须来自已提交的干净检出，并与发布门禁使用同一个完整 candidate SHA。

## 构建与发布门禁

Release ZIP 是可重复生成的部署产物，不提交到 Git。在候选版本的干净检出中运行：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.46
```

输出为 `deploy/cloudbase/fitness-agent-backend-0.5.46.zip`。核对包内 `app/build_metadata.json` 的 `build_version`、`build_commit` 和 `source_dirty=false`；小程序构建也必须对应 `0.5.46` 与相同完整 SHA。打包脚本检查敏感文件、测试目录、缓存、Python 字节码、必需文件和 ZIP 边界；成功打包本身不证明发布门禁已通过。

正式发布前，在 GitHub `main` 分支手动运行 `Daily Meal Live Model Release Gate`，将已提交候选的完整 SHA 填入 `candidate_sha`。该工作流检出指定 SHA，先验证全领域真实模型语义路由，再对同一 SHA 顺序执行两轮全天饮食端到端评测；每轮都要求原句 10/10、20 条同义表达至少 19/20、优化器不可用次数为零，并验证未确认写入和意外 Proposal 均为零。评测报告只包含脱敏状态和统计，不记录个人资料、食品候选或模型原文。迁移、部署和回退仍需在与生产配置对应的隔离环境演练。

## 配置与已验范围

两份根目录示例配置与当前代码默认值一致：

```dotenv
# 兼容项已弃用，普通语义路由不以规则作为执行授权。
AGENT_RULES_FIRST_ENABLED=false
AGENT_INTENT_TIMEOUT_SECONDS=10
AGENT_INTENT_TOTAL_TIMEOUT_SECONDS=24
AGENT_INTENT_ROUTE_TIMEOUT_SECONDS=14
AGENT_INTENT_ROUTE_MAX_TOKENS=1000
# 结构化变更提取预算，与紧凑路由预算分别配置。
AGENT_INTENT_MAX_TOKENS=1100
AGENT_INTENT_RETRY_MIN_REMAINING_SECONDS=2
```

示例文件不会覆盖已有部署环境变量。部署时核对实际生效值；本轮没有修改生产配置或正在运行的 LAN 配置。

写入提案能力分别控制，部署基线全部关闭：

```dotenv
AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED=false
MANUAL_PLAN_PROPOSALS_ENABLED=false
AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED=false
AGENT_PROFILE_PROPOSALS_ENABLED=false
AGENT_WEIGHT_PROPOSALS_ENABLED=false
AGENT_NUTRITION_PROPOSALS_ENABLED=false
```

本地 LAN 验收在独立数据库和合成账号中显式启用所需提案能力，验证了计划、餐食和查询链路；这不是生产开关启用凭据。手机文字回报覆盖约定的计划显示、旧卡导航、查询显示与退出重开，计划手机确认按钮仍未覆盖；桌面 A36 点击观察继续保留。按领域灰度前还需完成对应环境与用户操作验收。

提案读取与修改入口分别处理。关闭开关不会删除已存提案，但不能承诺所有已有提案仍可确认或拒绝：旧计划提案接口的确认和拒绝同样检查 `AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED`。停用能力前必须核对待处理提案及对应接口行为；Agent 没有直接修改业务表的通用工具。

## 0032 迁移与 worker 切换

`0032_agent_queue_position.py` 在 `agent_runs` 新增可空的 `queue_position`，并增加同会话位置唯一约束及正数检查约束。新版本的任务排序和 `/ready` 查询依赖该列，必须先迁移、再启动新版本处理请求。

1. 在入口层暂停新的 Agent 请求，检查 `queued` / `running` 任务并等待完成；无法排空时，先制定并演练剩余任务处置，不能直接当作已完成或清除。`AGENT_ENABLED=false` 不等于停止入队，`AGENT_ASYNC_WORKER_ENABLED=false` 也只停止相应进程启动 worker，不替代入口控制。
2. 停止全部旧 API / worker 进程，保存数据库备份及旧版本产物、完整 SHA 和部署配置记录。避免新旧 worker 同时消费同一队列。
3. 由一个迁移执行者运行 `python -m alembic upgrade head`，再以 `python -m alembic current` 核对版本为 `0032`。可以使用一次性迁移任务；若临时设置 `RUN_DB_MIGRATIONS_ON_STARTUP=true`，只启动一个迁移执行者，成功后后续实例设为 `false`。`scripts/start_server.sh` 会在启动 uvicorn 前执行迁移，不应让多实例同时尝试迁移。
4. 启动新版本，先核对 `/health` 和 `/ready`。`/ready` 检查必要列可查询，并返回 `agent_worker_enabled` 配置值；它不等于 worker 活性检查，也不替代 Alembic 版本核对。
5. 在隔离环境验证正常 API 入队、worker 领取、租约续期、终态和重复提交结果，检查同会话任务顺序及提案确认边界；需要启用写入提案的验证仍限定合成账号与隔离数据库。生产启动后按已审查的运行验证方案观察队列是否前进、是否发生持续重试或积压，再恢复流量。worker 开关在进程启动时读取，改变配置需要重启对应进程。

## 回退限制

出现异常时先暂停新的 Agent 请求并停止新 worker，保留任务、提案、日志和数据库证据。优先评估应用版本回退并保留 `0032` 的新增列，避免直接丢失队列位置信息；这是待演练的回退策略，旧应用与新增 schema、任务状态及当前数据的兼容性尚不能视为已验证。

不要在新版本进程仍运行时执行数据库 downgrade。`0032` 的 downgrade 会删除 `queue_position` 及约束，丢失已有排序信息，之后重新 upgrade 不会自动恢复这些值。确需退回 `0031` 时，必须在无业务流量、全部相关 worker 停止、备份可恢复且独立演练通过的条件下执行，并在重新开放前复核未完成任务与提案状态。生产数据库迁移和回退不由本说明自动执行。

生产密钥只配置在腾讯云托管环境变量中。不要把 `.env.production`、AppSecret、数据库连接密码或模型 API Key 放进部署包或 Git 历史。
