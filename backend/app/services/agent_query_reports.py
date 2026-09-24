"""Fact-only reports for bounded read requests, using ordinary audited tools."""
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from app.services.workout_history_reporting import history_limit, render_history
from app.services.workout_reporting import progress_request, render_progress, report_today


@dataclass(frozen=True)
class QueryReport:
    kind: str
    tool_id: str
    arguments: dict
    query: str
    scope: Any = None


def select_query_report(query: str, allowlist: list[str]) -> QueryReport | None:
    if not allowlist or set(allowlist) - {'workout.get_progress', 'workout.list_history', 'workout.get_daily_context'}:
        return None
    # Advice, writes, action-specific statistics, and mixed goals need the
    # existing semantic/planned execution path; a summary cannot answer them.
    if any(word in query for word in ('建议', '推荐', '制定', '修改', '删除', '记录一', '计划', '饮食', '疼痛', '如何', '怎么办')):
        return None
    scope = progress_request(query, report_today())
    limit = history_limit(query)
    if scope and limit:
        return None
    if limit and 'workout.list_history' in allowlist:
        return QueryReport('history', 'workout.list_history', {'limit': limit}, query)
    if scope and 'workout.get_progress' in allowlist:
        if any(word in query for word in ('动作', '深蹲', '卧推', '硬拉', '体重', '单次', '逐日', '每天', '明细', '每次', '列出', '训练日', '今日')):
            return None
        return QueryReport('progress', 'workout.get_progress', {'weeks': scope.weeks}, query, scope)
    return None


def report_for_resolution(resolution, allowlist, *, allow_redundant=False):
    if (resolution.risk_level != 'low' or resolution.clarification_required
            or resolution.request_kind not in {'query', 'assessment'}
            or resolution.requested_effect != 'read' or resolution.change_requests
            or resolution.intent_domain not in {'workout_progress', 'workout_history'}):
        return None
    report = select_query_report(resolution.resolved_query, allowlist)
    if report and report.kind == 'progress' and not allow_redundant and allowlist != ['workout.get_progress']:
        return None
    return report


def narrow_report_evidence(resolution, message: str):
    """Remove redundant evidence for a single bounded report; never grant tools."""
    from app.services.agent_intent import route_tools
    report = report_for_resolution(resolution, route_tools(resolution), allow_redundant=True)
    if report is None:
        return resolution
    # The user's original text can include a second goal omitted by the model.
    if any(word in message for word in ('明细', '每次', '每天', '今日', '计划', '资料', '目标', '饮食', '建议')) and report.kind == 'progress':
        return resolution
    evidence = 'workout_progress' if report.kind == 'progress' else 'workout_history'
    return resolution.model_copy(update={'evidence_requirements': [evidence]})


async def execute_query_report(report: QueryReport, tools: list) -> dict:
    # Tool construction enforces the same per-request allowlist and argument
    # schema as the normal agent loop. No unregistered direct DB calls here.
    name = report.tool_id.replace('.', '_')
    tool = next(item for item in tools if item.name == name)
    data = await tool.ainvoke(report.arguments)
    if report.kind == 'progress':
        answer = render_progress(data, report.scope, report.query)
    else:
        answer = render_history(data, report.query)
        if answer is None:
            answer = (f'已查询最近{report.arguments["limit"]}次训练，但未找到能与所问动作准确对应的记录。'
                      '请提供记录中的动作名称；暂时不能据此比较重量变化。')
    call_id = 'report-' + uuid4().hex
    # Protocol messages preserve the existing card/audit/trace pipeline. They
    # are explicitly marked as program output and are not counted as LLM calls.
    return {'response_mode': f'verified_{report.kind}_report', 'messages': [
        AIMessage(content='', tool_calls=[{'name': name, 'args': report.arguments, 'id': call_id}]),
        ToolMessage(content=json.dumps(data, ensure_ascii=False, default=str), name=name, tool_call_id=call_id),
        AIMessage(content=answer),
    ]}
