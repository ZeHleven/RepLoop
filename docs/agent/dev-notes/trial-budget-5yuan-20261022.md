# 体验版费用兜底：每账号累计5元

日期：2026-10-22登记，2026-10-23完成本地验收（America/Los_Angeles）；问题编号 COST-20261022-01；状态：本地候选通过，生产未部署。

## 怎么发现

用户询问体验成员恶意消耗模型额度的风险，随后明确要求“费用兜底，每个人最多用5元”。静态核对最近交付0.5.46/部署066后端ZIP、配置、路由、worker和模型客户端：既有每Run调用预算不能限制持续创建新任务；没有按用户累计费用账本。不是模型错误、不是运行压测结果。生产控制台当前状态尚未实时复核。

真实任务是体验成员连续发问，包括跨会话、并发、模型重试。预期：账号累计可用模型费用最多人民币5元；实际：新任务可以继续访问模型。

## 修改前的计划

- 基于生产已交付commit 7ec084818208cea6bb5c7d756be274ccdec43e25独立候选，保留根工作区与冻结试用源。
- PostgreSQL持久账本，以服务端认证user_id归属；累计额度固定5元，不随换会话、日期或重启重置。从新功能启用开始计账，旧记录不足以准确补算全部HTTP重试/模型费用。
- 每次实际模型HTTP请求发送前原子预留；调用后按可验证用量和保守价格结算。并发共享余额，失败/超时/未知用量不自动返还不明费用；用量不可信时停止该账号后续调用。
- 覆盖同步Agent、异步worker、旧聊天和其他用户模型入口；费用不足时返回清楚的体验额度提示。新增迁移、必要后端接口与测试；前端只改需要的错误展示。
- 官方价格来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ ，2026-10-22读取：Flash峰时每百万缓存命中0.04元、未命中2元、输出8元；Pro为0.30/9/27元。保守上界计费不等于供应商实际账单。未知模型、计费入口或价格状态须拒绝调用。
- 验收：真实独立PostgreSQL中的原子竞争/持久化/用户隔离，离线HTTP替身检查调用前拒绝、重试、异常与结算；相关既有回归。真实模型调用0，生产部署单独记录。

## 具体怎么修改

新增0033迁移、model_budgets账户表和model_charges逐HTTP尝试表。金额用整数纳元保存，LIMIT_NANO=5,000,000,000；PostgreSQL行锁保证消费＋预留＋新请求上界不超过额度。额度固定在服务端，无客户端重置/补额入口。

服务端认证后绑定计费账号，异步worker从持久Run读取user_id。直接HTTP、两个LangChain客户端、旧聊天、训练计划生成与饮食建议共用模型传输层。GET /api/v1/agent/budget返回本人余额；worker失败结果和同步402均显示5元额度提示。内部意图回退不能掩盖费用拒绝。/ready现在要求账本表存在。

请求只接受已知DeepSeek官方文本聊天入口和已核价模型；输出最多4096 tokens，保留更小的已有设置；输入以完整JSON UTF-8字节数×4＋8192作为保守预留估计，限制估计上下文在1M以内。所有输入按峰时缓存未命中价结算，输出按峰时价；不使用低谷/缓存折扣，因此这是保守额度口径，非供应商账单。实际响应模型必须匹配已核价别名，用量必须为非负整数且合计一致。未知模型/网关/媒体/服务器工具/流式请求被拒绝；用户请求的Embedding因无已核价费率被阻断（最近已保存生产配置RAG=false）。SDK重试仍经过同一传输层。

返回有效usage时释放多余预留；明确HTTP错误保守计入整笔预留，原有受限回退可以继续且逐请求记账；超时/取消/未知用量保留预留并暂停账号。进程直接死亡至少保留预留，不返还不明费用。异常超过估计上界则如实记录并暂停；不能声称此软件估计是供应商账单的数学硬上界。模型价格/分词或响应语义变更须重新核价；已落账不会随新会话或重启重置。

修改前源码保留为source-before.tar，来自7ec0848；SHA256 3a0e0b2ba63f01b194409da4689444ac472e0f98bdab7c6d36512e762e15c2e0。工作树异步创建期间先在本轮artifacts/candidate中实现，完成后同步独立工作树；根backend和冻结539来源未修改。

失败/调整：初次Docker普通权限启动未保持运行，使用受审查的提升权限启动现有Docker；中断后引擎停止，再启动。默认网络池已满，首次容器创建未启动；读取既有网段后用不冲突10.253.48.0/28建立专用internal网络，未删除或改动旧网络。初次迁移日志仅重定向stdout遗漏stderr，随后重新收集完整容器日志。没有测试失败；不是模型错误或评测器误判。云控制台打开失败：net::ERR_SSL_PROTOCOL_ERROR，未绕过安全警告、未上传或部署。

## 效果如何

同一合成账号20个并发预留请求、每个1元：5个成功，15个在发送前拒绝，账本总预留5元。不同账号各有独立5元。完整相关回归最终77/77，包含31项新增费用测试；首批26/26是其子集，不累加为103项。覆盖实际LangChain SDK、结构化HTTP回退、同步Agent、异步队列worker、旧聊天、并发、身份隔离、缺用量/无效用量、超时、取消、数据库不可用、未知模型及媒体。模型服务均为替身，真实调用0、费用0。

0032→0033隔离迁移通过，既有合成用户字段逐值不变；数据库重启＋新进程后已结算0.000360000元及未完成预留1元保持，超额预留仍拒绝，/ready=200。

## 怎么验收

证据目录：artifacts/agent-dev-notes/trial-budget-5yuan-20261022/。

- 首批：容器reploop-budget-tests-20261023-a，python -m pytest tests/test_model_budget.py -q；26/26，tests-first.log/xml。
- 最终：容器reploop-budget-tests-20261023-b，python -m pytest tests/test_model_budget.py tests/test_ai_client.py tests/test_agent_async_jobs.py tests/test_agent_router_v1.py tests/test_agent_runtime_v1.py tests/test_chat_router.py tests/test_planner_service.py tests/test_nutritionist_service.py -q；77/77，tests-regression.log/xml。62条既有JWT utcnow弃用警告，非失败。
- 迁移：专用budget_migration库先alembic upgrade 0032，再python /evidence/verify_migration.py；原始migration-0032.log、migration-0033.log与migration-result.json。
- 重启：docker restart reploop-budget-db-20261022，再以新容器运行python /evidence/verify_restart.py；restart-result.json。
- 原始替身模型请求/响应：raw-synthetic-http.json。没有真实模型或业务工具调用轨迹可冒称为新模型评测；脚本断言、SQL和JUnit均保留。

所有测试使用本轮专用PostgreSQL，无宿主端口、网络internal、空模型密钥（针对鉴权分支的测试仅临时synthetic值）、来源只读挂载。没有生产数据或旧试用数据库连接。未覆盖：当前生产配置、实际供应商账单、真实模型质量、多账号冒用同一自然人、供应商未来调价、其他网关/Embedding计费。只有一账号5元，不是已完成自然人身份去重。

## 配图与发布素材

本地草稿trial-budget-5yuan-wechat-draft.md；配图assets/trial-budget-5yuan.svg是合成测试数据图，不是产品或手机截图；原文索引publication-excerpts.json。仅本地素材，不发布。

## 下一轮

候选与发布包备妥后，待云控制台连接恢复，先核对当前生产仍为对应基线、备份和待处理任务；0033迁移后部署本候选，确认旧API/worker退出、全部流量切换、新版本日志与ready及拒绝路径。旧版本不执行费用保护，混跑不安全；回滚应用会失去费用保护，必须暂停模型入口且保留账本，禁止降级删除账本表。当前不能宣布体验版每人5元已生效。
