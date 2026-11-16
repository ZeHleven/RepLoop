# 项目文档

日常从本页进入。版本、验收结论和待办统一维护在 [当前状态](current-status.md)；历史报告保留各自的样本、版本和时间范围。

| 用途 | 入口 |
| --- | --- |
| 工作区、发布版本、待发布候选与剩余问题 | [当前状态](current-status.md) |
| 主工作区、本地启动、依赖和版本核对 | [运行指南](getting-started.md) |
| Agent 架构、工具契约与评测规范 | [Agent 文档](agent/README.md) |
| 小程序开发 | [小程序说明](../miniapp/README.md) |
| 后端打包与部署配置 | [部署包说明](../deploy/cloudbase/README.md) |
| 旧体验版部署架构参考 | [体验版发布参考](experience-release.md) |
| 最新一次客户端构建核验记录 | [071 同步构建](miniapp-sync-071-20261109.md) |
| 评测脚本和真人反馈采集 | [评测器说明](../backend/evals/README.md)、[体验指标采集](../scripts/agent_experience/README.md) |

## 保留的项目约定

- 微信小程序是当前产品入口；客户端负责交互，训练业务与数据规则集中在后端。
- Agent 通过领域工具读取事实；意图路由、工具权限、提案确认与幂等是独立边界。
- 测试通过、真实模型任务完成、手机体验、生产部署分别记录；历史成绩只对应当时的提交与样本。
- 新改动围绕实际问题和验收结果展开，不再维护逐轮开发笔记或自动生成公众号素材。

## 历史和个人参考

- [历史文档归档 ZIP](../artifacts/_archive/docs-history-20261111.zip)：83 份旧评测、交付单、规划和阶段状态，保持归档前原文及原路径。解压到独立目录查阅；文件清单与 SHA256 见 [归档清单](../artifacts/_archive/docs-history-20261111.manifest.json)。
- [架构审查与面试问答](../artifacts/reference/interview/13-architecture-review-and-interview.md)、[Skills、架构、记忆与评测面试材料](../artifacts/reference/interview/14-interview-skills-architecture-memory-evaluation.md)：2026-09-16 的个人参考，项目事实对应当时工作区。
- [原始评测证据](../artifacts/agent-dev-notes/)继续保留，包含响应、工具轨迹、测试日志和代码指纹；该目录名不代表恢复已删除的开发笔记。
