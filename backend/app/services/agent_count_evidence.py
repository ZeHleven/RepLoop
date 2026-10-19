"""Attest a complete bounded history as evidence for a session-count obligation.

This does not authorize tools, certify the answer, or substitute detailed
history for trends, averages, volume, completion rates or personal intensity.
"""
import json
import re
from datetime import date
from langchain_core.messages import ToolMessage
from app.schemas.agent_trace import AgentCountEvidenceSubstitutionTrace
from app.services.agent_trace import observation_fingerprint
from app.services.business_clock import business_today
from app.services.history_status_scope import parse_history_status_scope, effective_history_statuses, STATUS_LABELS, DEFAULT_STATUSES


def substitution_is_attested(trace, proof):
    """A persisted proof must still reference its exact successful observation."""
    return any(o.call_id == proof.call_id and o.tool_id == proof.observed_tool
               and o.status == 'success' and o.result_fingerprint == proof.result_fingerprint
               for o in trace.observations) and any(
        a.call_id == proof.call_id and a.tool_id == proof.observed_tool and a.status == 'completed'
        for a in trace.actions)


def _count_scope(query, today):
    from app.services.agent_query_reports import explicit_history_range
    from app.services.workout_reporting import progress_request
    from app.services.agent_task_evidence import count_evidence_query
    query = count_evidence_query(query)
    # A quoted example is not an instruction to count its contents. Preserve
    # only quoted enum labels (e.g. 状态为“已完成”), which are actual operands.
    query = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"',
        lambda m: m.group()[1:-1] if m.group()[1:-1] in (*STATUS_LABELS.values(),'已经完成','正在进行') else ' ',query)
    if not re.search(r'(?:几次|多少次)训练|训练(?:次数|场次)|已完成(?:训练)?场次|(?:完成|计划)(?:训练)?日|(?:练|训练)了几次', query):
        return None
    if re.search(r'趋势|平均|每周|按周|强度|容量|负重|重量|组数|重复次数|完成率|出勤率|最好|最佳|动作|RPE|RIR', query, re.I):
        return None
    status = parse_history_status_scope(query)
    if not status.complete:
        return None
    bounds = explicit_history_range(query)
    if bounds is None:
        if re.search(r'\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日', query):
            return None
        period = progress_request(query, today)
        if not period or period.compare:
            return None
        bounds = period.start, period.end
    start, requested_end = bounds
    end = min(requested_end, today)
    if start > end:
        return None
    return start, end, requested_end, tuple(status.statuses or DEFAULT_STATUSES)


def history_count_observation(data, args, scope, today):
    """Validate actual history scope; a partial count is never a complete count."""
    start, end, requested_end, wanted = scope
    try:
        tool_end = date.fromisoformat(data['range_end'])
        actual_statuses, sessions = data['status_filter'], data['sessions']
        if (data['range_start'] != start.isoformat() or not end <= tool_end <= requested_end
                or data['as_of'] != today.isoformat() or data['timezone'] != 'Asia/Shanghai'
                or args.get('start_date') != data['range_start'] or args.get('end_date') != data['range_end']
                or type(data.get('truncated')) is not bool or not isinstance(sessions, list)
                or type(data.get('count')) is not int or type(data.get('total_count')) is not int
                or data['count'] != len(sessions) or data['total_count'] < data['count']
                or data['truncated'] != (data['total_count'] > data['count'])
                or not isinstance(actual_statuses, list) or len(set(actual_statuses)) != len(actual_statuses)
                or set(actual_statuses) != set(effective_history_statuses(args.get('statuses'), completed_only=args.get('completed_only', False)))
                or not set(wanted) <= set(actual_statuses)):
            return None
        ids = [s['id'] for s in sessions]
        if (any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids)
                or any(s['status'] not in STATUS_LABELS or s['status'] not in actual_statuses
                       or not start <= date.fromisoformat(s['trained_at']) <= end for s in sessions)):
            return None
        return sum(s['status'] in wanted for s in sessions), not data['truncated']
    except (KeyError, TypeError, ValueError):
        return None


def count_substitution(trace, result, query):
    contract = trace.evidence_contract
    if (not contract or contract.status == 'blocked' or trace.execution_mode != 'direct'
            or trace.terminal_action != 'answer' or result.get('read_error_code')
            or 'workout.get_progress' not in contract.required_tools):
        return None
    today = business_today()
    if not trace.request_context or trace.request_context.as_of != today.isoformat():
        return None
    scope = _count_scope(query, today)
    if not scope:
        return None
    start, end, requested_end, wanted = scope
    for message in result.get('messages', []):
        if (not isinstance(message, ToolMessage) or message.name != 'workout_list_history'
                or message.status != 'success'):
            continue
        observed = [o for o in trace.observations if o.call_id == message.tool_call_id
                    and o.tool_id == 'workout.list_history' and o.status == 'success']
        actions = [a for a in trace.actions if a.call_id == message.tool_call_id
                   and a.tool_id == 'workout.list_history' and a.status == 'completed']
        if len(observed) != 1 or len(actions) != 1:
            continue
        fingerprint = observation_fingerprint(message.content)
        if fingerprint != observed[0].result_fingerprint:
            continue
        try:
            data = json.loads(message.content)
            count = history_count_observation(data, actions[0].arguments, scope, today)
            if count is None or not count[1]:
                continue
            return AgentCountEvidenceSubstitutionTrace(
                call_id=message.tool_call_id, result_fingerprint=fingerprint,
                range_start=start.isoformat(), range_end=end.isoformat(), as_of=today.isoformat(),
                statuses=list(wanted), session_count=count[0],
            )
        except (KeyError, TypeError, ValueError):
            continue
    return None
