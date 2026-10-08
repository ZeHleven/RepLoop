"""Task evidence obligations survive routing, budgets, and model completion."""
from app.schemas.agent_trace import AgentEvidenceContractTrace, AgentExecutionTrace
from app.services.agent_intent import IntentResolution, MAX_ROUTED_TOOLS, required_read_tools

TOOL_LABELS = {
    "profile.get_summary": "个人资料", "health.get_screening_summary": "健康限制",
    "weight.list_history": "体重记录", "plan.get_active": "当前计划",
    "workout.get_next": "下一练", "workout.get_active_session": "当前训练",
    "workout.list_history": "训练历史", "workout.get_progress": "训练进度",
    "workout.get_daily_context": "当天训练", "nutrition.get_today": "当天饮食",
    "nutrition.list_history": "饮食历史", "nutrition.get_recent_context": "近期饮食",
    "food.search": "食品信息", "food.list_candidates": "食品候选",
}


def build_evidence_contract(resolution: IntentResolution, allowlist: list[str], *, budget: int):
    required = required_read_tools(resolution)
    if not required:
        return None
    missing = [tool for tool in required if tool not in allowlist]
    reason = ("budget_exceeded" if len(required) > min(MAX_ROUTED_TOOLS, budget)
              else "authority_missing" if missing else None)
    return AgentEvidenceContractTrace(
        required_tools=required, missing_tools=required if reason else [],
        status="blocked" if reason else "pending", reason=reason,
    )


def missing_evidence_reply(tools: list[str], *, blocked: bool = False) -> str:
    labels = "、".join(TOOL_LABELS.get(tool, "所需资料") for tool in tools)
    if blocked:
        return f"这次需要结合{labels}，目前无法在一轮内完整核对。请先选最想了解的部分，我会按这个范围继续。"
    return f"这次还没有完整取得{labels}，暂时无法可靠完成这项查询或评估。请稍后重试缺少的部分。"


def check_evidence(trace: AgentExecutionTrace) -> AgentExecutionTrace:
    contract = trace.evidence_contract
    if contract is None or contract.status == "blocked":
        return trace
    successful = {observation.tool_id for observation in trace.observations
                  if observation.status == "success"}
    missing = [tool for tool in contract.required_tools if tool not in successful]
    return trace.model_copy(update={"evidence_contract": contract.model_copy(update={
        "missing_tools": missing, "status": "partial" if missing else "complete",
        "reason": "observations_missing" if missing else None,
    })})


def guard_read_completion(reply: str, trace: AgentExecutionTrace):
    trace = check_evidence(trace)
    contract = trace.evidence_contract
    if contract and contract.status == "partial" and trace.terminal_action in {"answer", "proposal"}:
        finalization = trace.finalization_contract
        if finalization is not None:
            finalization = finalization.model_copy(update={
                "allowed_outcomes": ["insufficient_evidence"],
                "selected_outcome": "insufficient_evidence", "derived_terminal_action": "answer",
            })
        trace = trace.model_copy(update={
            "terminal_action": "answer", "termination_reason": "required_evidence_missing",
            "finalization_contract": finalization,
        })
        reply = missing_evidence_reply(contract.missing_tools)
    return reply, trace
