# trial-before-clarification-local-fix-2b

2026-10-18｜本地收口进行中；不编号 R157，不做付费验收。

发现与实际差异：上一刀冻结回放 HO04 通过、HO06 漏检；当前症状和必要确认提取均为空，动作一致性检查在读取最终回答中的试练许可前提前返回。原始证据在 `artifacts/agent-dev-notes/trial-before-clarification-local-fix-2/`，本轮不另改症状词表或识别规则。

最小计划：仅修改 `agent_action_consistency.py` 的提前返回及现有试练许可与未确认动作来源的衔接。当前症状/提问提取为空不应绕过最终回答检查；明确确认后的方案、已确认做法及普通无症状减量继续允许。沿用单次纠正和既有兜底，不修改临床、器械来源、guard_fallback 或 unnecessary_clarification。

验收条件：先复现原有 HO06 漏检及独立同类控制流用例；补丁冻结后 HO04/HO06 原始边界回放必须 2/2，上一刀 34 相关回归＋固定 4 核心 smoke 与新增定向用例全绿。近期已通过原题若回退，先回退候选再收窄。真实基线仍为 guard-fallback-acceptance 27/30、5/10、红线 0、连续 0/2；本轮 ¥0、累计约 ¥14.90/¥60。未全绿不写完成；结束即停等用户。
