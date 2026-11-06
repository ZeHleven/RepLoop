# 第157轮：发布门禁计费身份与可选症状调用的费用失败

日期：2026-11-05（America/Los_Angeles）。问题编号：BUDGET-20261105-01、BUDGET-20261105-02。
状态（提交前验收快照）：本地修复和验收完成，用户随后已明确授权提交并推送修复分支、运行普通CI；未部署，新候选的付费真实模型门禁仍需另行确认。提交后的CI结果以该分支对应的GitHub运行页面为准。

## 怎么发现

候选 `51feceb570b8d0d374948c5bf0558201958d1d4f`（0.5.47）的 [GitHub run 42140355885](https://github.com/ZeHleven/RepLoop/actions/runs/42140355885) 失败。路由33项仅3项通过，30项出现 ModelBudgetError / 规则回退；饮食两轮62/62通过。原始结论：

```text
Live release gate failed: route=failure, daily_meal=success
##[error]Process completed with exit code 1.
```

continue-on-error 使步骤 conclusion 显示成功，实际 outcome 和最终门禁为失败。先前进度中把路由误报为成功，已向用户更正；本记录以下载的原始 JSON 为准。

路由脚本直接调用 resolver，未建立 billing_user。ModelBudgetError 在访问模型与账本之前触发；不能把30项失败解释为模型不理解问题。生产同步入口和 worker 均经 execute_agent_run 绑定认证用户，不存在同样的普遍身份缺失。

另一条生产运行路径存在独立问题：可选症状调用使用另一个 BillingScope，但账本仍按同一个 user_id 共享。超时或未知用量会暂停共享账本；独立 scope 隐藏了当前请求的费用失败，当前 Run 可显示成功，下一次模型调用却被拒绝。旧测试只人工设置 scope.failure，没有检查真实 SQL 账本。

真实样本是上述发布门禁；线上问题用合成用户、原始膝痛任务与模拟故障在独立 PostgreSQL 中复现，未读取生产日志/账本，不能说已发生线上事故。

## 修改前的计划

本节于业务代码修改前登记，原始计划保存在本轮过程记录中。

- 在独立检出 RepLoop-budget-fix、分支 codex/model-budget-release-gate 上修复；基线为上述完整 SHA。保留原工作区、发布检出和 ZIP。
- 路由评测在隔离本地库创建一个合成账户；全部 case 共用该账户累计费用，每题建立计费上下文，不逐题重置预算。拒绝生产库/远程库，保持原 candidate_sha 和判定标准。
- 症状提取与主请求共享费用失败状态：费用不可核实时当前 Run 明确失败；已知费用、仅解析失败时仍规则降级。
- 保留每账号累计5元、逐次 HTTP 原子预留、未知费用保留预留并暂停、结算失败不退款等规则。原设计明确估算预留不是供应商账单的数学硬上界，不能扣估算金额后放行。
- 保存修改前真实 SQL 失败；覆盖同步/worker、超时/缺少 usage、同账户后续拒绝、其他账户不受影响、已知费用解析失败降级。
- 不设置用户或系统环境变量，不改 .env / 生产配置 / 迁移，不提交、不推送、不发布。使用进程内测试设置与 MockTransport，新增真实模型费用为0。

## 具体怎么修改

业务改动仅三个文件：

1. `backend/scripts/evaluate_agent_routing_real.py`：校验 PostgreSQL 驱动、本机地址、评测库后缀和无 URL 参数；创建无登录凭据的 UUID 用户；所有 case 使用一个账户、逐题 billing_user。数据库拒绝发生在创建账号或调用模型之前。
2. `backend/app/services/agent_runtime.py`：症状子任务继承同一个 BillingScope，不再新建独立 scope。既有 execute_agent_run 费用失败处理可观察子任务失败。
3. `backend/app/services/agent_symptom_facts.py`：明确传播 ModelBudgetError；已核实费用后的语义/解析失败仍返回 None，由规则处理。

没有修改 model_budget.py、限额、迁移、workflow、部署脚本或生产环境。旧的“可选调用失败仍成功”测试更新为共享费用失败；新增真实 ASGI / worker / BudgetTransport / PostgreSQL 集成测试，以及评测入口账户与安全库校验测试。

测试库为本轮创建的 `reploop-r157-budget-db` 容器中的 `reploop_r157_test`，宿主仅绑定127.0.0.1:55457。没有挂载既有数据库卷。仅在该新容器内为专用数据库放行 Docker 网关访问；未操作生产数据库。启动 Docker Desktop 后其既有容器按原有启动策略启动，未主动调整这些容器。

测试启动器通过进程内 Settings 和 SQL fixture 注入隔离连接，禁用真实 HTTP；不写任何环境变量。测试自身的 monkeypatch 在临时进程内自动恢复。Python3.13全量使用缓存镜像 `fitness-agent-backend-ci:0.5.36-energy-local` 的解释器/依赖，挂载的是当前修复源码，源目录只读；这不是新构建的发布镜像。

## 效果如何

独立SQL的四种组合（sync/worker × timeout/missing_usage）都复现和验证了以下变化：

| 项目 | 修改前 | 修改后 |
| --- | --- | --- |
| 当前 Run | completed，无错误码 | failed，model_budget_unavailable |
| 同步接口 | HTTP 200 | HTTP 503 |
| worker 查询 | HTTP 200，状态 completed | HTTP 200，状态 failed |
| 共享账本 | blocked=true | blocked=true，保留保护 |
| 本次预留/实际入账 | 68,680,000 nano / 0，charge=uncertain | 原样保留 |
| 同账号后续调用 | 模型之前拒绝 | 模型之前拒绝 |
| 另一合成账号 | 模拟HTTP200 | 模拟HTTP200 |
| 产生训练提案 | 0 | 0 |

68,680,000 nano 是合成请求的预留量，不是实际模型费用或供应商账单。

已知 usage、仅症状 JSON 解析失败的样本：Run completed，两次实际模拟 HTTP 调用都 settled，reserved=0，blocked=false，规则降级成功。路由评测两个合成 case 使用同一个持久用户，两条预留累计为200,000 nano。没有通过关闭费用保护换取通过。

## 怎么验收

所有原始文件保存在本地 `artifacts/agent-dev-notes/round-157-budget-release-gate/`（未纳入本次提交）；命令见其中 `commands.md`。以下测试有重叠，98项是1993项的一部分，不相加计算覆盖率。

| 尝试 | 结果 | 判定 |
| --- | --- | --- |
| 修改前SQL首轮 | 15 errors | 新测试库 HBA 未允许 Docker 网关，环境夹具错误 |
| 修改前SQL第二轮 | 9 failed / 5 passed / 1 error | 5项评测入口失败、4项费用失败被隐藏；另1项 Windows 临时目录权限错误 |
| 修改后定向回归 | 98 passed，61 warnings | 路由入口、SQL症状、事实提取、原预算规则通过 |
| Python3.13全量首轮 | 1986 passed / 7 failed | 启动器全局替换了库校验函数，干扰安全校验测试；已保留日志 |
| Python3.13全量第二轮 | 1993 passed，877 warnings | 改为仅为SQL fixture绑定连接，原安全校验函数及测试保留 |
| 合成任务首轮 | 22 passed / 8 failed | pytest按另一个模块名导入，输出目录未注入；多轮报告缺失，不能记作通过 |
| 合成任务第二轮 | 30 passed，130 warnings | 15完整任务、5多轮任务、8修订任务均有原始报告；另含2项校验测试 |
| 确定性规则 | 39/39 | strict退出0，各准确率1.0、越界/误写指标0 |
| 原凭据扫描首轮 | 退出1，WinError1920 | pytest生成的Linux链接在Windows无法读取 |
| 公开源码副本扫描 | 退出0 | 原脚本未改；完整源码副本排除本地 artifacts，文件清单及哈希已保存 |

warning 主要是既有 jose UTC 弃用警告和启动器预加载模块的 pytest 断言重写提示，不将其说成零警告。

关键原始报错见日志：

```text
asyncpg.exceptions.InvalidAuthorizationSpecificationError: no pg_hba.conf entry for host "172.17.0.1", user "fitness", database "reploop_r157_test", no encryption
AssertionError: live route evaluation omitted billing identity
AssertionError: unsafe evaluation reached database or model
AssertionError: optional facts hid an account-level budget refusal
E   Failed: DID NOT RAISE <class 'RuntimeError'>
E   KeyError: 'REVISION_EVAL_DIR'
OSError: [WinError 1920] 系统无法访问此文件。
```

准备阶段还遇到历史 artifacts 权限拒绝、缺少模板、git archive 参数解析、一次编排 SyntaxError、Docker 尚未启动、一次自动审批超时、猜测文件路径不存在。工具/夹具错误与产品缺陷分别记录；没有因工具报错改动产品逻辑、验收阈值或删除失败证据。详见 `tool-errors.md`。数据库 HBA、输出路径和测试注入修正只影响本轮专用测试环境。

证据索引：

- `source-before.tar`：业务修改前保存的相关原代码。SHA256 D4728443E48E5CB2222C809C8BD80497393D40A5950CDAD8158F8E9435D11715。
- `source-before-supplement.tar`：后来从不可变基线 SHA 补充提取的两个原测试文件；不冒称是修改前新采集。
- `source-after.tar`、`source-tracked-changes.patch`：7个当前代码/测试文件和已跟踪文件差异；新测试包含在tar中。
- `baseline-agent-route-live-eval.json`、`baseline-daily-meal-live-eval.json`：原GitHub报告，未修改。原路由报告未提供模型原始响应，不补造。
- `tests-*.log/xml`、`test-attempts.json`：完整失败/成功输出和汇总。
- `synthetic-raw-before.json`、`synthetic-raw-after.json`：从带行号日志提取的合成请求、模拟模型原始响应、接口响应、执行轨迹和账本断言；原日志中重复输出保留。
- `before-after-comparison.json`：按 endpoint/failure 去重后的4组前后对照。
- `scripted-after-02/`：任务模型输出、HTTP轨迹、SQL结果和报告；不包含 Authorization。
- `source-excerpts.json`、`sha256-manifest.json`：配图依据与文件完整性记录。

未覆盖：新提交的 GitHub CI、新候选的付费真实模型门禁、生产账本/日志、真实模型并发延迟和长期运行、手机体验版验收。本轮没有执行迁移、任何 downgrade、重新构建生产镜像或 miniapp 构建。没有把本地合成测试说成线上成功率。

## 配图与发布素材

本地 `round-157-wechat-draft.md` 和 `assets/round-157-budget-release-gate.svg/png` 为本轮素材，均未纳入本次提交。图片明确标注“机制示意图 · 合成故障注入 · 非小程序截图”，由同一份文字和几何数据生成；`source-excerpts.json` 与原始日志可回溯。没有发布或向外部服务发送。

## 下一轮

用户已审阅本地结果并授权提交、推送修复分支；取得新候选 SHA 后验证 GitHub CI。未获合并或发布授权；付费门禁需要针对新候选再次确认。全部通过后从干净新 SHA 打包，不能把当前旧 ZIP 冒充已含修复。

旧包的严格“任何密码都没有”条件仍不满足（开发/评测固定值、示例值）；本轮没有擅改打包脚本或删除ZIP内容。上线前还需处理该验收项。发布和云托管上传由用户本人完成，环境变量暂时无需调整。发布待办清单保存在本地 `round-157-release-checklist.md`，未纳入本次提交。
