"""Fact-only reports for bounded read requests, using ordinary audited tools."""
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from app.services.workout_history_reporting import history_limit, render_history, render_calendar_history
from app.services.workout_reporting import progress_request, render_progress, report_today, scope_progress
from app.services.history_status_scope import parse_history_status_scope


_CALENDAR_DATE = re.compile(r'\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日')


def _unbounded_nutrition_listing(query: str) -> bool:
    # A finite listing vocabulary proves that no unknown date, meal, metric,
    # or other selector was discarded. Unrecognized requests use the agent.
    remaining = re.sub(
        r'有记录的|已记录的|记录过的|已记录|最近|近期|列一下|列出|查看|查询|看看|展示|显示|'
        r'请|帮我|给我|我的|我|饮食|营养|日期|历史|日志|记录|汇总|和|及|与|的',
        '', query,
    )
    return not remaining.strip(' \t\r\n，,。；;、！!？?')


@dataclass(frozen=True)
class QueryReport:
    kind: str
    tool_id: str
    arguments: dict
    query: str
    scope: Any = None


def current_query_goal(query: str) -> str:
    # Only discard a complete cancelled goal, never a word inside an active goal.
    cancelled = r'(?:先)?(?:不再|不用|不需要|不|取消)(?:比较|对比|比)(?:单个|这个|某个)?动作(?:了)?'
    return '，'.join(part.strip() for part in re.split(r'[，,。；;]', query)
                    if part.strip() and not re.fullmatch(cancelled, part.strip()))


def select_query_report(query: str, allowlist: list[str]) -> QueryReport | None:
    query = current_query_goal(query)
    status_scope = parse_history_status_scope(query)
    if not status_scope.complete:
        return None
    status_arguments = {}
    if status_scope.statuses == ('completed',):
        status_arguments['completed_only'] = True
    elif status_scope.statuses is not None:
        status_arguments['statuses'] = list(status_scope.statuses)
    if not allowlist or set(allowlist) - {'workout.get_progress', 'workout.list_history', 'workout.get_daily_context'}:
        return None
    # Advice, writes, action-specific statistics, and mixed goals need the
    # existing semantic/planned execution path; a summary cannot answer them.
    if any(word in query for word in ('建议', '推荐', '制定', '修改', '删除', '记录一', '计划', '饮食', '疼痛', '如何', '怎么办')):
        return None
    bounds = explicit_history_range(query)
    if _CALENDAR_DATE.search(query) and bounds is None:
        # Unsupported date selections must not fall through to a weekly
        # aggregate, which can otherwise widen two separate days into a week.
        return None
    if allowlist == ['workout.list_history'] and not any(word in query for word in ('比较', '对比', '动作', '深蹲', '卧推', '硬拉', '体重', '目标', '趋势', '分析')):
        if bounds:
            arguments = {'limit': 20, 'start_date': bounds[0].isoformat(), 'end_date': bounds[1].isoformat()}
            arguments.update(status_arguments)
            return QueryReport('calendar_history', 'workout.list_history', arguments, query)
    scope = progress_request(query, report_today())
    limit = history_limit(query)
    if scope and limit:
        return None
    if limit and not status_arguments and 'workout.list_history' in allowlist:
        return QueryReport('history', 'workout.list_history', {'limit': limit}, query)
    if scope and 'workout.get_progress' in allowlist:
        if any(word in query for word in ('动作', '深蹲', '卧推', '硬拉', '体重', '单次', '逐日', '每天', '明细', '每次', '列出', '训练日', '今日')):
            return None
        return QueryReport('progress', 'workout.get_progress', {'weeks': scope.weeks}, query, scope)
    if scope and allowlist == ['workout.list_history']:
        # Calendar history is distinct from "latest N". Keep advice, comparisons
        # and multi-goal requests on their existing semantic execution path.
        if any(word in query for word in ('比较', '对比', '动作', '深蹲', '卧推', '硬拉', '体重', '目标')):
            return None
        return QueryReport('calendar_history', 'workout.list_history', {
            'limit': 20, 'start_date': scope.start.isoformat(), 'end_date': scope.end.isoformat(), **status_arguments,
        }, query, scope)
    return None


def explicit_history_range(query: str) -> tuple[date, date] | None:
    # Normalize spacing within Chinese calendar dates, not arbitrary numbers.
    query = re.sub(r"(?<=\d)\s+(?=[年月日])|(?<=[年月])\s+(?=\d)", "", query)
    iso = re.findall(r'\d{4}-\d{2}-\d{2}', query)
    chinese = re.findall(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日', query)
    calendar_dates = list(_CALENDAR_DATE.finditer(query))
    if len(calendar_dates) == 2:
        connector = query[calendar_dates[0].end():calendar_dates[1].start()]
        if not re.fullmatch(r'\s*(?:至|到|~|～|—|–|-)\s*', connector):
            return None
    try:
        if len(iso) == 2 and not chinese:
            start, end = (date.fromisoformat(item) for item in iso)
        elif len(chinese) == 2 and not iso:
            year = int(chinese[0][0] or report_today().year)
            start, end = (date(int(y or year), int(m), int(d)) for y, m, d in chinese)
        elif (len(iso) + len(chinese) == 1
              and not re.search(r'至|到|~|～|—|–|起|从|自|以来|之前|之后|以前|以后|日[前后]|最近|过去|上周|本周|\d{4}-\d{2}-\d{2}\s*[-前后]', query)):
            # A fully specified single day is a closed interval. Do not turn
            # an open or shorthand range (e.g. 9月12日至14日) into one day.
            if iso:
                start = end = date.fromisoformat(iso[0])
            else:
                y, m, d = chinese[0]
                start = end = date(int(y or report_today().year), int(m), int(d))
        else:
            return None
        return (start, end) if 0 <= (end-start).days < 366 else None
    except ValueError:
        return None


def report_for_resolution(resolution, allowlist, *, allow_redundant=False):
    if (resolution.risk_level == 'low' and not resolution.clarification_required
            and resolution.request_kind == 'query' and resolution.requested_effect == 'read'
            and not resolution.change_requests and resolution.intent_domain == 'nutrition'
            and allowlist == ['nutrition.list_history']
            and resolution.evidence_requirements == ['nutrition_history']):
        query = resolution.resolved_query
        # Only recognized unbounded listings. A blacklist cannot prove that
        # an ISO date, relative period, or meal-specific request has no scope.
        if _unbounded_nutrition_listing(query):
            return QueryReport('nutrition_history', 'nutrition.list_history', {'days': 30}, query)
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
    if report.kind == 'nutrition_history':
        return resolution
    # The user's original text can include a second goal omitted by the model.
    if any(word in current_query_goal(message) for word in ('明细', '每次', '每天', '今日', '计划', '资料', '目标', '饮食', '建议', '动作', '深蹲', '卧推', '硬拉')) and report.kind == 'progress':
        return resolution
    evidence = 'workout_progress' if report.kind == 'progress' else 'workout_history'
    return resolution.model_copy(update={'evidence_requirements': [evidence]})


async def execute_query_report(report: QueryReport, tools: list) -> dict:
    # Tool construction enforces the same per-request allowlist and argument
    # schema as the normal agent loop. No unregistered direct DB calls here.
    name = report.tool_id.replace('.', '_')
    tool = next(item for item in tools if item.name == name)
    data = await tool.ainvoke(report.arguments)
    display_data = data
    if report.kind == 'nutrition_history':
        answer = render_nutrition_history(data)
    elif report.kind == 'progress':
        display_data = scope_progress(data, report.scope)
        answer = render_progress(display_data, report.scope, report.query)
    elif report.kind == 'calendar_history':
        answer = render_calendar_history(data)
    else:
        answer = render_history(data, report.query)
        if answer is None:
            answer = (f'已查询最近{report.arguments["limit"]}次训练，但未找到能与所问动作准确对应的记录。'
                      '请提供记录中的动作名称；暂时不能据此比较重量变化。')
    call_id = 'report-' + uuid4().hex
    # Protocol messages preserve the existing card/audit/trace pipeline. They
    # are explicitly marked as program output and are not counted as LLM calls.
    return {'response_mode': f'verified_{report.kind}_report',
            'report_cards': [{'type': report.tool_id, 'data': display_data}], 'messages': [
        AIMessage(content='', tool_calls=[{'name': name, 'args': report.arguments, 'id': call_id}]),
        ToolMessage(content=json.dumps(data, ensure_ascii=False, default=str), name=name, tool_call_id=call_id),
        AIMessage(content=answer),
    ]}


def render_nutrition_history(data: dict) -> str:
    scope = data['scope']
    days = data['days']
    if not days:
        return f"截至{scope['as_of']}，还没有已记录的饮食。"
    lines = [f"截至{scope['as_of']}，最近有饮食记录的{len(days)}个日期如下："]
    for day in days:
        lines.append(f"- {day['date']}：{day['total_calories']:g} 千卡，"
                     f"蛋白质 {day['total_protein_g']:g} g，"
                     f"碳水 {day['total_carbs_g']:g} g，脂肪 {day['total_fat_g']:g} g。")
    lines.append(f"记录日期范围：{scope['first_logged_date']} 至 {scope['last_logged_date']}。"
                 f"最多展示{scope['requested_limit']}个有记录日期，未记录日期未纳入汇总。")
    return "\n".join(lines)
