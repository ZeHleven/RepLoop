# 第一版工具契约

## 对模型开放的工具

第一版工具注册表包含以下查询与提案能力：

| 工具 ID | 唯一适用场景 |
| --- | --- |
| `profile.get_summary` | 查询用户基础资料和训练偏好摘要 |
| `health.get_screening_summary` | 查询健康筛查和训练限制摘要 |
| `plan.get_active` | 查询当前活动计划的概要 |
| `plan.get_detail` | 已知计划 ID 时读取完整计划 |
| `workout.get_next` | 查询下一次应执行的训练内容 |
| `workout.get_active_session` | 查询正在进行的训练及已记录组 |
| `workout.list_history` | 查询近期训练场次列表 |
| `workout.get_exercise_history` | 查询某一动作的历史重量、次数和个人最佳 |
| `workout.get_progress` | 查询指定周期的训练次数、组次和容量聚合 |
| `exercise.search` | 按名称、器械和限制查找动作库候选项 |
| `plan.propose_personalized` | 根据资料生成但不保存个性化计划提案 |
| `plan.propose_adjustment` | 根据训练结果生成但不写回下一练调整提案 |

## 保留但默认隐藏的执行工具

`plan.confirm`、`workout.start`、`workout.record_set`、`workout.complete` 等执行契约保留在架构中。启用条件为：专项评测通过、功能开关开启、路由白名单命中、用户明确确认、参数再次校验并携带幂等键。

## 契约规则

- 每个工具使用严格 Pydantic 输入/输出模型，拒绝额外字段。
- 描述必须包含适用场景、不适用场景、前置条件和一到两个例子。
- 工具不接收模型提供的 `user_id`；服务端从认证上下文注入，防止越权。
- 查询工具无副作用；提案工具不得提交数据库事务。
- 执行工具返回 `executed`、业务资源 ID 和幂等结果，不能用自然语言暗示成功。
- 超时、无数据、权限、校验和业务冲突使用稳定错误码，供 Agent 选择澄清或降级。
