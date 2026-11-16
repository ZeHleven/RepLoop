# Agent 真实体验指标

本轮固定版本：51feceb570b8d0d374948c5bf0558201958d1d4f。原应用不修改；观察器在隔离进程中关联 TCP API、Run、worker、工具审计、逐 HTTP 模型调用与费用账本。结果目录：`artifacts/agent-dev-notes/current-experience-metrics-20261108`。

本轮已完成：live-02为16任务、14完整完成/2部分完成、70模型HTTP，估算¥0.452758。13项单测和产物对账通过。模型与数据原文保留，测试数据库已停止。真人反馈仍为0条；[完整报告](../../docs/agent/reviews/2026-11-08-current-experience-metrics.md)包含版本边界、首次401、时钟异常及全部分母。

## 当前版本基线

`tasks.json` 在真实输出前固定8题，顺序和并发2各一次。它是合成开发基线，不是用户满意度、盲测或生产压测。数据、模型模式、版本、并发组分开统计。观察器会增加少量日志和归属查询开销，不据此作精确优化归因。

`launch.py` 只读取指定配置文件中的模型密钥，不读取其中的数据库配置。它只允许固定的独立Docker数据库和干净源码副本。输出目录不得存在，数据库必须没有历史Run，以避免旧排队任务混入。

以下是本轮已执行的命令记录，不应为了查看报告重新运行真实模型：

```powershell
.venv/Scripts/python.exe -m unittest discover -s scripts/agent_experience -p 'test_*.py' -v
.venv/Scripts/python.exe scripts/agent_experience/launch.py offline --batch offline-01
.venv/Scripts/python.exe scripts/agent_experience/launch.py live --batch live-01
# 首批401停止；用户选择.env后，保留首批占额再启动独立数据库中的新批次。
.venv/Scripts/python.exe scripts/agent_experience/launch.py live --batch live-02 --key-source .env --database-tag retry --max-cny 4.802944 --max-requests 158
```

只读复算不调用模型：

```powershell
.venv/Scripts/python.exe scripts/agent_experience/metrics.py artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02/result.json --reviews artifacts/agent-dev-notes/current-experience-metrics-20261108/reviews.json --feedback artifacts/agent-dev-notes/current-experience-metrics-20261108/feedback.json --out artifacts/agent-dev-notes/current-experience-metrics-20261108/metrics.json
.venv/Scripts/python.exe scripts/agent_experience/audit_artifacts.py
```

`verify_persistence.py` 是本轮模型测量后的隔离数据库补验，已经执行：读取活动计划/唯一餐次，并以ASGI尝试确认旧提案；外部HTTP被禁止。它不启动worker，不是额外真实模型批次，耗时不加入性能分布。原始plan快照没有活动状态字段，补验保存该状态，避免将历史版本误判为重复写入。

## 指标解释

- 完成：逐任务满足全部预登记目标；多轮合并计算，写入目标需确认后回读。模型HTTP成功、工具成功和Run completed不等于任务成功。
- 部分/失败/待补/待确认/合理拒绝/未知分别保留。未执行任务不删除；缺评审的任务为未知。`benchmark_complete=false` 时比例不可解释为正常Agent成功率。
- 客户端可见等待：提交前到轮询取得终态；本地TCP，不含手机公网、微信转发、渲染。任务主动耗时包括多轮及首次确认，不包括用户思考、夹具和导出。
- 排队与处理：保留实际Run时间，缺失、负值和时钟异常不填0。样本P95使用最近秩；每组仅8任务时相当于该组最大值，不是生产SLO。
- 成本：全部模型HTTP尝试含症状、意图、执行、SDK重试。应用保守费率估算与实际供应商账单区分；用量未知保留占额、停止新请求。每成功任务摊销成本包含失败任务费用，完整费用未知或零成功时为null。

## 真人反馈

`feedback.json` 初始为 `[]`，不能填入合成数据。正常使用后，每个完整目标记录一条；可先按以下格式向本聊天提供，随后依据实际Run关联复核：

```text
大致时间/会话：
目标：一句话即可，不必附私密健康原文
结果：完成 / 部分 / 没完成 / 不确定
纠正了Agent几次：0 / 数量 / 不确定
因它没理解而重复说明几次：0 / 数量 / 不确定
等待感受：可接受 / 偏慢 / 无法完成
满意度：1–5 / 不评价
是否明确因本次体验放弃：是 / 否 / 不确定
```

正常改主意不是纠错；必要补充信息不是重复说明；沉默不是满意或放弃。每条保留实际部署SHA与来源引用。手机秒数只能来自真实操作记录或计时埋点，不能挪用本地HTTP时长。目前没有修改小程序、接入线上反馈API或自动采集真人隐私。

版本更新后需建立新的冻结协议与数据库；不得把不同提交或新旧模型配置的结果合并。真实模型失败原样保留，不自动修复或反复跑到通过。
