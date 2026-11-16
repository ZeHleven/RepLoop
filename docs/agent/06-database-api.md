# Agent 数据库与 API 协议

## 数据边界

现有用户、资料、动作、计划和训练表继续作为业务真相。Agent 新增的数据只描述会话、运行、调用、提案和记忆，不复制训练业务实体。

计划中的 Agent 表：

- `agent_conversations`：用户会话、摘要和待澄清状态。
- `agent_messages`：用户、助手和工具消息。
- `agent_runs`：一次请求的意图、路由、模型、安全和结果状态。
- `agent_tool_calls`：工具名、脱敏参数、结果摘要、耗时和错误。
- `agent_proposals`：待确认、已接受、已拒绝或已过期的结构化提案。
- `agent_memories`：经确认的长期记忆、来源、置信度和状态。

所有表包含用户归属、创建时间；敏感字段最小化并支持删除。真实写操作必须带幂等键，提案接受使用版本号防止并发重复执行。

## API

- `POST /api/v1/agent/runs`：生产客户端提交消息；必须带 `client_request_id`，在持久化后立即返回 `202`、会话 ID、运行 ID 和建议轮询间隔。
- `GET /api/v1/agent/runs/{id}`：读取 `queued/running/completed/failed` 状态；完成后同时返回文本和可选卡片。
- `POST /api/v1/agent/chat`：同步兼容接口，仅用于旧客户端和定向调试，不再用于微信小程序生产链路。
- `GET /api/v1/agent/conversations/{id}/messages`：分页读取消息。
- `POST /api/v1/agent/proposals/{id}/confirm`：未来用于确认并执行已启用的写提案。
- `POST /api/v1/agent/proposals/{id}/reject`：拒绝提案，不修改业务数据。

第一版只实现聊天和只读/提案路径。认证继续复用现有 JWT；客户端不能指定其他用户的数据范围。

`agent_runs` 同时承担持久化任务队列：用户与幂等键联合唯一，worker 使用数据库行锁和带期限租约领取任务。容器中断后，租约过期的任务可被其他实例接管；达到最大尝试次数后进入稳定失败状态。

Alembic `0015` 为会话增加 `pending_clarification`，并为 run 增加 `resolved_query`、`references`、`clarification_question` 和 `understanding_version`，使指代、扩展、拆解与澄清决策可以回放和评测。
