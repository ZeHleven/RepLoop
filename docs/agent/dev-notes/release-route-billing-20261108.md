# 正式路由门禁的计费上下文修复

日期：2026-11-08 America/Los_Angeles。问题：EVAL-20261108-01。状态：修改前登记。属于已授权“修复→合并与正式门禁→部署→手机采集”中的发布门禁修复；当前线上未动。

## 怎么发现

PR #46已合并为a65a3ff42e4f9084f2dda09c1db6f401cd0174d5，PR及主线CI都通过2082项后端与小程序检查。正式工作流42577336410最终失败：路由3/33、关键3/9、30项ordinary_rules_fallback，30项error_category均ModelBudgetError；两轮全天饮食分别原句10/10、同义21/21，写入/意外提案/优化器不可用均0。

静态证据：evaluate_agent_routing_real.evaluate直接调用resolve_intent_with_fallback，没有billing_user；BudgetTransport在scope为空时于发送provider请求前拒绝。正常运行时按run.user_id进入billing_user，饮食评测走正常运行时所以有上下文。需要用不付费的真实计费传输层回归证明，而不是关闭计费或把ModelBudgetError视为模型回答错误。

原工作流使用continue-on-error，中间API step.conclusion显示success但最终step.outcome为failure。此前进度曾误称路由通过，已明确更正；以后只读完整报告和最终裁决。一次读取回执的自动审批因Bad Request未执行，重新提交同一只读操作审批后成功，未绕过。

## 修改前的计划

1. 保留a65a3ff干净检出、路由脚本/计费传输层修改前源码和SHA、正式脱敏报告及完整工作流日志。新建独立codex分支，原历史修复及实测检出不改。
2. 新增不付费回归，覆盖真实BudgetTransport预留/结算、同一合成账号的未知费用阻断，以及非本地/非评测数据库在创建账号或模型调用前拒绝。先在旧脚本运行并保存失败。
3. 仅修评测器：在明确隔离的本地PostgreSQL评测库建一个合成账号，调用时进入既有billing_user；保持所有case、expected、通过阈值、生产计费与Agent运行代码不变。账号沿整批共享费用上限，不为每题重置额度。报告只保留不含账号标识的账本汇总；保留缺用量时的未知/保守预留，不填0费用。
4. 相关计费/评测回归及完整GitHub CI通过后合并。核对全部backend/app与f4d388f实测版本逐字相同。对新合并完整SHA再执行一次正式工作流；不按模型答案改题、放宽门槛或循环重跑。本地付费调用仍184次/¥1.227114；正式门禁为先前登记的独立流程，原失败批成本因旧报告未输出账本仍未知，不能声称总费用只有1.23元。
5. 新SHA正式门禁通过后才生成包与交接部署；若仍失败，保存原因，不把失败包装为通过。

验收：改前真实计费边界回归失败；改后有实际合成用户、预留/结算可核对，未知费用不继续发provider请求；33题集合/预期及阈值不变；所有生产app源码不变；正式报告candidate_sha与将来包一致。

## 具体怎么修改

待实施。

## 效果如何

原正式结果必须保留为失败，不能用两轮饮食全过覆盖路由门禁失败。真实语义路由表现尚无这一SHA的有效分数；3/33是流程受阻结果。

## 怎么验收

证据目录：artifacts/agent-dev-notes/release-route-billing-20261108。原门禁报告：artifacts/agent-dev-notes/history-intent-release-20261108/formal-gate。无provider原始回复的正式脱敏报告不能替代本地逐条响应，未采集范围如实记录。

## 配图与发布素材

完成后在本地开发笔记草稿补充发布门禁这一段。历史查询指标示意仍是本地合成任务，不改成线上体验图，不发布公众号。

## 下一轮

继续同SHA正式验收、部署与实机采集；生产部署仍需控制台人工操作，工具因Windows当前URL无法可靠识别已停止，未绕过。

## 本地修复结果

仅修改backend/scripts/evaluate_agent_routing_real.py，并新增test_route_eval_billing.py。脚本拒绝非本地、非明确评测名称的PostgreSQL库；先提交合成用户，再对整套题共享该账号，逐次进入既有billing_user。新增脱敏账本汇总，未知费用为null，保留预留和阻断，不重置额度。

新增5项改前全部失败：真实BudgetTransport因缺计费账号拒绝、后续场景无法形成可计费请求，3项新隔离库前置约束尚不存在。这是一个评测集成问题及新增边界，不能说是5个模型错误。改后本文件5项及既有路由/饮食评测/计费合计50项通过、5条既有warning。已知费用正确结算；缺usage后共享账号继续阻断，provider模拟层只收到1次请求，第二题不再发送。没有真实模型费用。

121份backend/app与a65a3ff（对应f4d388f实测运行代码）逐字一致；33题CASES AST和passed判分表达式AST完全一致。命令为`.venv/Scripts/python.exe scripts/history_intent_release/run_route_billing_regression.py before`与`after`；before.log/xml、after.log/xml及source-verification.json保留。首次本地git clone受限MSYS初始化失败，正常权限的本地克隆成功，无源码或数据丢失。

完整GitHub CI与新SHA的正式真实门禁待执行。旧a65a3ff正式门禁仍记失败，原62项饮食通过不跨SHA冒充新候选验收。原正式报告没有完整费用，不推断账单。

素材：[本地草稿](release-route-billing-20261108-wechat-draft.md)，配图引用已留原文的[上一阶段指标示意](assets/history-intent-release-20261108/metrics.png)，明确只表示历史查询本地结果；本次门禁故障另用流程图示意，非产品截图。未发布。
