"""Task evidence obligations survive routing, budgets, and model completion."""
from app.schemas.agent_trace import AgentEvidenceContractTrace, AgentExecutionTrace
from app.services.agent_intent import IntentResolution, MAX_ROUTED_TOOLS, required_read_tools, EVIDENCE_TOOL_ALLOWLIST

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
        optional_tools=[tool for e in resolution.optional_evidence_requirements
                        if e in resolution.evidence_requirements
                        for tool in EVIDENCE_TOOL_ALLOWLIST[e]],
        scope_quotes=resolution.read_scope_quotes,
    )


def missing_evidence_reply(tools: list[str], *, blocked: bool = False, risk_level: str = "low") -> str:
    labels = "、".join(TOOL_LABELS.get(tool, "所需资料") for tool in tools)
    # Clinical precautions do not depend on successful business-data reads.
    # These are general thresholds, not inferred symptoms or a diagnosis.
    care = (
        "资料不足不能作为继续训练的依据。请先停止会诱发或加重不适的训练。"
        "新近受伤后若有明显肿痛、活动受限或承重困难，请及时就医评估，"
        "不要等症状加重或等查询恢复。"
        "若出现胸痛、呼吸困难、意识异常或突发一侧无力、说话异常，"
        "请立即联系所在地急救服务（中国大陆拨打120），不要自行开车。\n\n"
    ) if risk_level == "medium" else ""
    if blocked:
        return care + f"这次需要结合{labels}，目前无法在一轮内完整核对。请先选最想了解的部分，我会按这个范围继续。"
    return care + f"这次还没有完整取得{labels}，暂时无法可靠完成这项查询或评估。请稍后重试缺少的部分。"


def check_evidence(trace: AgentExecutionTrace, *, result=None, query: str = "") -> AgentExecutionTrace:
    contract = trace.evidence_contract
    if contract is None or contract.status == "blocked":
        return trace
    successful = {observation.tool_id for observation in trace.observations
                  if observation.status == "success"}
    from app.services.agent_count_evidence import count_substitution, substitution_is_attested
    substitutions = [proof for proof in contract.substitutions if substitution_is_attested(trace, proof)]
    if result is not None and query:
        proof = count_substitution(trace, result, query)
        if proof is not None:
            substitutions = [p for p in substitutions if p.required_tool != proof.required_tool]+[proof]
        from app.services.agent_nutrition_evidence import nutrition_substitutions
        for proof in nutrition_substitutions(trace, result, query):
            substitutions = [p for p in substitutions if p.required_tool != proof.required_tool]+[proof]
    successful.update(proof.required_tool for proof in substitutions)
    missing = [tool for tool in contract.required_tools if tool not in successful]
    return trace.model_copy(update={"evidence_contract": contract.model_copy(update={
        "missing_tools": missing, "status": "partial" if missing else "complete",
        "reason": "observations_missing" if missing else None,
        "substitutions": substitutions,
    })})


def guard_read_completion(reply: str, trace: AgentExecutionTrace, *, result=None, query: str = ""):
    trace = check_evidence(trace, result=result, query=query)
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
        reply = missing_evidence_reply(contract.missing_tools, risk_level=trace.risk_level)
    return reply, trace
