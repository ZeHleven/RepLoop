"""Read-only, no-argument obligations collected before an assessment answer."""
from dataclasses import dataclass, field
import json
import re
import time
from langchain_core.messages import AIMessage, ToolMessage
from langchain.agents.middleware import ToolCallLimitMiddleware, AgentMiddleware
from app.config import settings
from app.services.agent_intent import required_read_tools
from app.services.agent_read_recovery import invoke_report_read, ERROR_REPLIES
from app.services.agent_tools import NoArguments
from app.services.ai_client import AIServiceError
from app.services.agent_read_recovery import error_code


class AgentReadExecutionError(AIServiceError):
    """Safe failure message plus already-observed evidence for owned persistence."""
    def __init__(self, cause, partial_result):
        super().__init__('模型服务未能完成本次回答，已取得的查询证据仍保留，请稍后重试。')
        self.partial_result = partial_result
        self.provider_error_category = type(cause).__name__


class ReadEvidenceJournal(AgentMiddleware):
    """Record completed model/tool events even if a later graph node fails."""
    def __init__(self, messages, *, propagate=()):
        self.messages = list(messages)
        self.evidence_messages = [m.model_copy(deep=True) for m in messages
                                  if isinstance(m, ToolMessage)]
        self.propagate = propagate

    async def aafter_model(self, state, runtime):
        message = state.get('messages', [])[-1]
        if getattr(message, 'type', None) == 'ai':
            self.messages.append(message.model_copy(deep=True))
        return None

    async def awrap_tool_call(self, request, handler):
        started = time.perf_counter()
        try:
            result = await handler(request)
        except Exception as exc:
            if isinstance(exc, self.propagate):
                raise
            self.messages.append(ToolMessage(
                content=json.dumps({'error_code': error_code(exc)}), status='error',
                tool_call_id=request.tool_call['id'], name=request.tool_call['name'],
                additional_kwargs={'duration_ms': round((time.perf_counter()-started)*1000)},
            ))
            raise
        if isinstance(result, ToolMessage):
            result = result.model_copy(update={'additional_kwargs': {
                **result.additional_kwargs, 'duration_ms': round((time.perf_counter()-started)*1000),
            }}, deep=True)
            self.messages.append(result)
            self.evidence_messages.append(result.model_copy(deep=True))
        return result


class EvidenceToolLimit(ToolCallLimitMiddleware):
    """Keep the budget decision even when graph output hides middleware state."""
    def __init__(self, limit):
        super().__init__(run_limit=limit, exit_behavior='end')
        self.exhausted = False
        self.limit = limit

    def after_model(self, state, runtime):
        update = super().after_model(state, runtime)
        if update and update.get('jump_to') == 'end':
            self.exhausted = True
            calls = next((m.tool_calls for m in reversed(state.get('messages', []))
                          if isinstance(m, AIMessage) and m.tool_calls), [])
            used = state.get('run_tool_call_count', {}).get('__all__', 0)
            for message in update.get('messages', []):
                message.additional_kwargs['controller_generated'] = True
                if getattr(message, 'type', None) == 'tool':
                    message.additional_kwargs['executed'] = False
                    message.additional_kwargs['dispatch_stage'] = 'tool_budget_gate'
                    message.additional_kwargs['duration_ms'] = 0
                    message.content = json.dumps({
                        'error_code': 'read_tool_budget_exhausted', 'executed': False,
                        'dispatch_stage': 'tool_budget_gate', 'limit': self.limit,
                        'used_before': used, 'batch_call_ids': [c['id'] for c in calls],
                        'call_id': message.tool_call_id,
                    }, ensure_ascii=False)
        return update


@dataclass
class PreparedEvidence:
    tools: list
    active: bool = False
    messages: list = field(default_factory=list)
    remaining: int = 0
    transaction_reset: bool = False
    error_code: str | None = None
    pending_tools: list[str] = field(default_factory=list)

    def failure_result(self):
        return {'response_mode': 'verified_required_evidence_error',
                'read_error_code': self.error_code,
                'read_transaction_reset': self.transaction_reset,
                'messages': self.messages + [AIMessage(content=ERROR_REPLIES[self.error_code],
                    additional_kwargs={'controller_generated': True})]}


def _continued_obligations(resolution, task_state, query=''):
    from app.schemas.agent_task import TaskSnapshot
    from app.services.agent_task_state import active_task
    from app.services.agent_task_evidence import task_read_evidence
    from app.services.agent_intent import EVIDENCE_TOOL_ALLOWLIST
    # Only the server-hydrated, validated active task is eligible. Do not parse
    # assistant prose or model paraphrases as authority to collect private data.
    if not isinstance(task_state,TaskSnapshot) or task_state.transition not in {'continue','resume'}:
        return False
    task=active_task(task_state)
    if not task:return False
    source='；'.join(r.quote for r in task.requirements if r.kind=='goal')
    evidence,optional,anchors=task_read_evidence(source,domain=resolution.intent_domain,
        kind=resolution.request_kind,effect=resolution.requested_effect,risk=resolution.risk_level,evidence=[])
    backed={tool for e in evidence if e not in optional for tool in EVIDENCE_TOOL_ALLOWLIST[e]}
    # A source-checked edit can replace "临时训练建议" with a more specific
    # request. Use surviving user requirements, never the superseded quote or
    # the model-normalized task request, to recognize its explicit plan basis.
    plan_quotes = []
    # Extraction may omit the plan basis or split it from the requested
    # output. The current user's exact message remains a valid source for
    # this already-validated continuation; model paraphrases never do.
    sources = [r.quote for r in task.requirements if r.kind == 'goal']
    sources.append(query)
    for source_quote in sources:
        plain = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', source_quote)
        for clause in re.split(r'[，,。；;！？!?\n]', plain):
            if re.search(r'不要|不用|无需|不必|不再|暂不|先别|假如|假设|如果|例如|比如|朋友|别人', clause):
                continue
            if re.search(r'(?:根据|结合|参考|依据|按)[^。；\n]{0,30}(?:已存|已保存|现有|当前|我的|原|刚才查到)(?:的)?(?:训练)?计划[^。；\n]{0,30}(?:取舍|调整|回归|恢复|建议)', clause):
                plan_quotes.append(clause)
    if plan_quotes and resolution.intent_domain in {'health','workout_plan','workout_history','workout_progress','workout_session'}:
        backed.add('plan.get_active')
        anchors.extend(plan_quotes)
        # Re-read health only when the same active task has a current personal
        # symptom report. Old or hypothetical symptoms are not new assertions.
        from app.services.agent_symptom_context import current_symptom_quotes
        from app.services.business_clock import business_today
        if any(r.kind == 'goal' and r.as_of == business_today().isoformat()
               and current_symptom_quotes(r.quote) for r in task.requirements):
            backed.add('health.get_screening_summary')
    required=set(required_read_tools(resolution))
    return bool(anchors and required and required<=backed)


async def prepare_required_evidence(db, *, tools, resolution, allowlist, propagate=(), query='', task_state=None):
    result = PreparedEvidence(tools=list(tools), remaining=max(0, settings.AGENT_MAX_TOOL_CALLS))
    source_bound = bool(resolution and resolution.read_scope_quotes and query
                        and all(q and q in query for q in resolution.read_scope_quotes))
    legacy_assessment = bool(resolution and resolution.request_kind == 'assessment' and resolution.risk_level == 'low')
    continued = bool(resolution and _continued_obligations(resolution,task_state,query))
    if not (resolution and resolution.request_kind in {'query', 'assessment'}
            and resolution.requested_effect == 'read' and resolution.risk_level in {'low', 'medium'}
            and (source_bound or legacy_assessment or continued)
            and not (resolution.read_scope_quotes and not source_bound)
            and not resolution.change_requests and not resolution.clarification_required):
        return result
    # A validated continuation may inherit the original task's read obligation,
    # but not previous tool results or guessed calendar arguments. Re-read now.
    required = required_read_tools(resolution)
    if not required:
        return result
    result.active = True
    by_id = {tool.name.replace('_', '.', 1): tool for tool in tools}
    # Tool ids may contain underscores after the first namespace separator.
    if any(tool_id not in allowlist or tool_id not in by_id for tool_id in required):
        result.error_code = 'read_tool_permission_denied'
        return result
    if len(required) > result.remaining:
        result.error_code = 'read_tool_budget_exhausted'
        return result
    completed = set()

    async def read(tool_id, args, reserve):
        data, messages, reset, code = await invoke_report_read(
            by_id[tool_id], args, db=db, propagate=propagate,
            max_attempts=min(2, result.remaining - reserve),
        )
        result.messages.extend(messages)
        result.remaining -= sum(getattr(m, 'type', None) == 'tool' for m in messages)
        result.transaction_reset |= reset
        result.error_code = code
        if not code:
            completed.add(tool_id)
        return data

    for tool_id in required:
        tool = by_id[tool_id]
        # Never guess dates, counts, search terms, or any other model argument.
        if tool.args_schema is not NoArguments:
            continue
        reserve = len(required) - len(completed) - 1
        await read(tool_id, {}, reserve)
        if result.error_code:
            return result

    # Only original, source-bound count requests can supply these arguments.
    # A normalized model paraphrase is not authority for dates or statuses.
    from app.services.agent_count_evidence import _count_scope, history_count_observation
    from app.services.business_clock import business_today
    today = business_today()
    scope = _count_scope(query, today) if source_bound else None
    count_tools = {'workout.list_history', 'workout.get_progress'}
    if scope and count_tools <= set(required):
        start, end, _, statuses = scope
        args = {'start_date': start.isoformat(), 'end_date': end.isoformat(), 'statuses': list(statuses)}
        history_args = {'limit': 20, **args}
        reserve = len(set(required) - completed - {'workout.list_history'})
        data = await read('workout.list_history', history_args, reserve)
        if result.error_code:
            return result
        checked = history_count_observation(data, history_args, scope, today)
        if checked is None:
            result.error_code = 'read_tool_argument_invalid'
            return result
        if checked[1]:
            # The final trace must still independently attest the original
            # history message, call id, arguments and result fingerprint.
            completed.add('workout.get_progress')
        else:
            reserve = len(set(required) - completed - {'workout.get_progress'})
            aggregate = await read('workout.get_progress', args, reserve)
            if result.error_code:
                return result
            actual = aggregate.get('count_scope', {}) if isinstance(aggregate, dict) else {}
            if (not isinstance(aggregate, dict) or not isinstance(actual, dict)
                    or aggregate.get('aggregation') != 'all_matching_sessions'
                    or type(aggregate.get('total_sessions')) is not int or aggregate['total_sessions'] < 0
                    or any(actual.get(k) != v for k, v in args.items())
                    or actual.get('as_of') != today.isoformat() or actual.get('timezone') != 'Asia/Shanghai'):
                result.error_code = 'read_tool_argument_invalid'
                return result
    result.tools = [tool for tool in tools if tool.name.replace('_', '.', 1) not in completed]
    result.pending_tools = [tool_id for tool_id in required if tool_id not in completed]
    return result


async def complete_missing_required_evidence(db, *, result, tools, resolution, allowlist,
                                             remaining_model_calls, query='', propagate=()):
    """Complete only omitted, argument-free obligations before final delivery.

    Successful tasks and already attempted failures stay untouched. This is not
    another model tool loop: every missing read must fit the existing budgets.
    """
    completion = PreparedEvidence(tools=[])
    if (not resolution or resolution.request_kind not in {'query', 'assessment'}
            or resolution.requested_effect != 'read' or resolution.risk_level not in {'low', 'medium'}
            or resolution.change_requests or resolution.clarification_required
            or result.get('read_error_code') or remaining_model_calls < 1
            or (resolution.read_scope_quotes and not all(q and q in query for q in resolution.read_scope_quotes))):
        return completion
    required = required_read_tools(resolution)
    by_id = {tool.name.replace('_', '.', 1): tool for tool in tools}
    observations = [m for m in result.get('messages', []) if isinstance(m, ToolMessage)]
    attempted = {m.name.replace('_', '.', 1) for m in observations if m.name}
    successful = {m.name.replace('_', '.', 1) for m in observations
                  if m.name and m.status == 'success' and m.additional_kwargs.get('executed') is not False}
    missing = [tool_id for tool_id in required if tool_id not in successful]
    remaining = max(0, settings.AGENT_MAX_TOOL_CALLS - len(observations))
    if (not missing or len(missing) > remaining
            or any(t not in allowlist or t not in by_id for t in required)
            or any(t in attempted or by_id[t].args_schema is not NoArguments for t in missing)):
        return completion
    completion.active = True
    completion.remaining = remaining
    for tool_id in missing:
        _, messages, reset, code = await invoke_report_read(
            by_id[tool_id], {}, db=db, propagate=propagate, max_attempts=1,
        )
        completion.messages.extend(messages)
        completion.remaining -= sum(isinstance(m, ToolMessage) for m in messages)
        completion.transaction_reset |= reset
        if code:
            completion.error_code = code
            break
    return completion
