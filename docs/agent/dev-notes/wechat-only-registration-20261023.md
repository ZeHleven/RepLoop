# 体验版新注册仅保留微信

日期：2026-10-23（America/Los_Angeles）；问题编号 AUTH-20261023-01；状态：本地验收完成，待部署。

## 怎么发现

用户在每账号5元费用保护上线后询问注册方式。静态读取已部署b21ed9c来源与线上只读OpenAPI发现：小程序可微信首次登录建号，也可邮箱密码注册；服务端POST /api/v1/auth/register还接受手机号。邮箱仅格式验证、手机号无短信验证，后端没有新注册门禁。“开发调试”文字不是访问限制。用户明确要求“在服务端关闭邮箱和手机号的新注册，仅保留微信登录，并保留已有邮箱账号登录能力。”

真实任务是阻止通过普通注册接口反复新建账号获得5元额度；预期邮箱/手机号新注册都被拒绝且不写库，已有账号可登录，微信首次登录和重复登录保持。发现依据为代码和OpenAPI，不是恶意注册生产实测，不是模型错误。

## 修改前的计划

- 以已上线068对应b21ed9c费用保护代码为基线，复用本任务干净独立工作树建立新分支；修改前源码git archive留证，根backend和冻结试用来源不动。
- /auth/register改为统一403和明确中文提示，不接受新建账号，不依赖数据库或用户存在性查询；不保留可误开启的运行时注册开关。
- /auth/login、/auth/wechat、token刷新、用户数据及费用账本保持。仅服务端改动，不发布新的小程序；旧“创建账户”入口收到服务端关闭提示。
- 原测试大量通过公开注册接口准备已有账号，改为明确的测试夹具直接创建既有用户并走真实登录取token，不拦截或伪造/auth/register响应，不给产品增加测试后门。
- 验收：邮箱、手机号、组合、已有账号、缺字段及畸形body均拒绝，无用户/档案/身份/账本写入；旧邮箱和手机号账号登录/错误密码/token刷新保持；微信首次建号、重复身份复用、跨应用union关联及无效凭证行为保持；5元费用相关回归保持。使用隔离PostgreSQL与微信服务替身，真实微信/模型请求0、模型费用0。
- 无数据库迁移。发布包保留0033及费用保护，部署时启动迁移保持false；上线以安全的注册拒绝请求和既有合成账号登录/预算核验收尾。

## 具体怎么修改

运行代码仅修改backend/app/routers/auth.py：保留POST /auth/register路由，直接返回403及中文关闭提示，取消请求体身份解析与get_db依赖。login、wechat和refresh从/login装饰器起的代码内容与b21ed9c逐字节一致（忽略CRLF换行）。费用保护源码与0033迁移未改，没有新增迁移。

测试的21个文件变更包含1个运行文件、conftest、18个既有测试文件及1个新增策略测试文件。明确的AccountSeedClient.seed_existing_account仅在测试夹具中向隔离库准备既有账号，再调用真实/login；不拦截POST、不伪造注册HTTP响应、不给产品增加测试开关。旧注册成功/重复邮箱测试因策略退出由14项新策略测试替代，既有登录/token/微信测试保留。

根backend和冻结版本未修改。修改前完整源码source-before.tar来自b21ed9c，SHA256 399fc8802846b676b789b80ce79ed20fa9b2b89fa9dbc53a3cdcf63f812faaab。修改清单及前后文件哈希见change-manifest.json。

失败与调整：首次读取工作树和Docker需沙箱外权限，获自动审批后读取。修改前策略测试按新要求应失败13项、通过1项，证明缺口；不是模型失败。首次合并22文件回归在输出部分进度后退出139，OOM=false、无断言失败汇总且没有完整JUnit；保留tests-after.log，不能当作通过。随后同一代码按文件新进程执行完整22文件，全部通过。原合并进程异常根因未定位，记录为测试进程问题，未删测试或放宽断言。

## 效果如何

新增策略测试：修改前13失败/1通过，修改后14/14。最终22文件分进程回归446/446，无失败、无错误、无跳过，含31项费用保护测试、既有认证与微信身份测试及所有调整夹具的文件。每个文件均保留原始日志和JUnit；首个异常合并进程不计入通过数字。

同一隔离接口回放：修改前邮箱/手机号两次请求均201、用户数增加2；修改后两次均403、用户数增加0。已有合成邮箱账号登录200且原消费1元/剩余4元保持；微信首次登录is_new_user=true、重复登录false。raw-auth-before.json、raw-auth-after.json保存实际ASGI响应，明确遮蔽token和合成密码；微信服务为替身，外部微信请求0、模型调用0、费用0。

## 怎么验收

本轮证据目录artifacts/agent-dev-notes/wechat-only-registration-20261023/。

- before：专用容器reploop-registration-before-20261023，python -m pytest tests/test_registration_policy.py -q --junitxml=/evidence/tests-before.xml；13失败/1通过。
- 初次合并回归：reploop-registration-after-20261023，测试清单test-files.json，退出139，tests-after.log，无完整结果。
- 最终：reploop-registration-isolated-20261023，python /evidence/run_isolated_regression.py，逐文件python -X faulthandler -m pytest <file> -q；22文件、446/446，isolated-regression-result.json及逐文件日志/XML，容器exit0。
- 回放：reploop-registration-trace-before/after-20261023，TRACE_STAGE=before/after、python /evidence/capture_auth_trace.py；before通过覆盖单个只读auth.py还原原注册行为，其他运行源码一致；after使用原样候选。两次各6请求，状态及用户计数断言通过。
- 源码边界：python review_candidate.py，确认唯一运行改动auth.py、登录/微信/刷新保持、无新增迁移；21个文件的前后哈希保留。

仅使用既有本任务internal Docker网络和新建隔离库wechat_registration_20261023，源码只读挂载，无生产连接和外部模型调用。未覆盖真实微信客户端回归、自然人多微信账号、平台体验成员管理和全局消费总额限制。线上尚未部署。两个历史开发脚本e2e_real_flow.py、observe_registry_read_enforcement.py仍依赖已退出的普通注册流程，部署后会按新策略403退出；本轮未运行或改造它们，不保留注册后门以兼容旧脚本。

## 配图与发布素材

本地草稿wechat-only-registration-wechat-draft.md；配图assets/wechat-only-registration.svg是合成流程示意图，不是小程序现场截图。源码、原始回放及publication-excerpts.json提供可追溯证据；不公开发布。

## 下一轮

准备已验收发布包，按用户此前偏好交接具体手工部署步骤（已有控制台浏览器连接反复超时）。保持RUN_DB_MIGRATIONS_ON_STARTUP=false，无需迁移或更新小程序。新版本承接100%流量后执行check_production_registration.py：已有合成邮箱账号登录、预算保持；用既有邮箱及短密码手机号探测注册拒绝，即使旧版本仍在线也不会意外新建账号。得到真实部署及HTTP证据后再宣布线上关闭。回退到068会重新开放普通注册，不把保留5元账本误认为注册门禁仍在。
