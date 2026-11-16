"""One bounded, parameter-only model turn for an already-required history read."""
import json
import re
from datetime import timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.services.business_clock import business_today
from app.services.history_status_scope import effective_history_statuses, parse_history_status_scope
from app.services.workout_reporting import number, progress_request

ARGUMENT_COMPLETION_TOOL_IDS = frozenset({'workout.list_history'})


def _history_constraints(queries: list[str]):
    """Reject unrepresentable known scopes before spending a model turn."""
    from app.services.agent_query_reports import explicit_history_range

    requests = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))
    if not requests:
        raise ValueError('required_read_request_missing')
    today = business_today()
    expected_bounds, expected_limits, expected_statuses = set(), set(), set()
    for query in requests:
        if re.search(
            r'(?:上|这|本|前|下|去|今|明)(?:个)?(?:月|年)|'
            r'\d{4}[/年]\d{1,2}[/月](?!\d{1,2}日)|'
            r'(?:上上|上|本|这|前一)?周[一二三四五六日天1-7]|'
            r'(?:\d+|[一二三四五六七八九十两]+)天前', query,
        ):
            raise ValueError('required_read_calendar_scope_unresolved')
        bounds = explicit_history_range(query)
        if bounds is None and re.search(r'\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日', query):
            raise ValueError('required_read_calendar_scope_unresolved')
        if bounds is None:
            period = progress_request(query, today)
            rolling_weeks = re.search(r'(?:最近|近|过去)\s*[\d一二三四五六七八九十两]+\s*(?:个)?周', query)
            if rolling_weeks or (period and period.compare):
                # Do not collapse comparisons, or replace rolling weeks with
                # natural weeks. These scopes stay on the ordinary executor.
                raise ValueError('required_read_calendar_scope_unresolved')
            if period:
                bounds = (period.start, min(period.end, today))
            else:
                days = re.findall(r'(?:最近|过去|近)\s*([\d一二三四五六七八九十两]+)\s*天', query)
                relative = re.findall(r'前天|昨天|今天', query)
                if len(days) == 1 and not relative and 1 <= number(days[0]) <= 366:
                    bounds = (today - timedelta(days=number(days[0]) - 1), today)
                elif len(relative) == 1 and not days:
                    day = today - timedelta(days={'今天': 0, '昨天': 1, '前天': 2}[relative[0]])
                    bounds = (day, day)
                elif days or relative:
                    raise ValueError('required_read_calendar_scope_unresolved')
        if bounds:
            expected_bounds.add(tuple(d.isoformat() for d in bounds))
        limits = re.findall(r'(?:最近|近|过去)\s*([\d一二三四五六七八九十两]+)\s*次', query)
        if limits:
            if len(limits) != 1 or not 1 <= number(limits[0]) <= 20:
                raise ValueError('required_read_limit_unresolved')
            expected_limits.add(number(limits[0]))
        status = parse_history_status_scope(query)
        if not status.complete:
            raise ValueError('required_read_status_scope_unresolved')
        if status.statuses is not None:
            expected_statuses.add(frozenset(status.statuses))
    if len(expected_bounds) > 1 or len(expected_limits) > 1 or len(expected_statuses) > 1:
        raise ValueError('required_read_conflicting_scopes')
    if expected_statuses and not expected_bounds:
        # The history tool supports status filters only with a calendar range.
        raise ValueError('required_read_status_scope_unresolved')
    return (next(iter(expected_bounds), None), next(iter(expected_limits), None),
            next(iter(expected_statuses), None))


def can_bind_history_arguments(queries: list[str]) -> bool:
    try:
        _history_constraints(queries)
    except ValueError:
        return False
    return True


def validate_read_arguments(tool_id: str, tool: BaseTool, arguments: dict, queries: list[str]) -> dict:
    if tool_id not in ARGUMENT_COMPLETION_TOOL_IDS:
        raise ValueError('required_read_argument_binding_unsupported')
    schema = tool.get_input_schema()
    properties = schema.model_json_schema().get('properties', {})
    if not isinstance(arguments, dict) or set(arguments) - properties.keys():
        raise ValueError('required_read_unknown_arguments')
    if not arguments:
        raise ValueError('required_read_arguments_missing')
    validated = schema.model_validate(arguments, strict=True).model_dump(mode='json')
    bounds, limit, statuses = _history_constraints(queries)
    # The binding is a model proposal. Never replace a known requested scope
    # with defaults, invented dates, or an extra completed-only restriction.
    if limit is not None and validated['limit'] != limit:
        raise ValueError('required_read_limit_changed')
    if statuses is not None:
        actual = effective_history_statuses(validated.get('statuses'),
                                            completed_only=validated.get('completed_only', False))
        if set(actual) != statuses:
            raise ValueError('required_read_status_scope_changed')
    elif validated.get('statuses') or validated.get('completed_only'):
        raise ValueError('required_read_unsourced_status_scope')
    if bounds:
        if (validated.get('start_date'), validated.get('end_date')) != bounds:
            raise ValueError('required_read_calendar_scope_changed')
    elif validated.get('start_date') or validated.get('end_date'):
        raise ValueError('required_read_unsourced_calendar_scope')
    elif 'limit' not in arguments:
        raise ValueError('required_read_default_scope_forbidden')
    return validated


async def bind_read_arguments(model, *, tool_id: str, tool: BaseTool,
                              queries: list[str], context: list) -> dict:
    """No tool execution, application retry, new authority, or answer in this turn."""
    _history_constraints(queries)
    response = await model.bind_tools([tool], tool_choice=tool.name).ainvoke([
        *context,
        SystemMessage(content=(
            '服务端正在补齐已确定必需但尚未调用的只读查询。本轮只能为指定工具提交参数，'
            '不能回答原问题、跳过读取或选择其他工具。参数必须来自用户请求、本轮已消解的语义'
            '及真实工具观察；保留日期、条数、状态和其他筛选条件，不用默认范围替代。'
            '用户对恢复情况的自述不是数据库观测，不要编造自述对应的日期或训练记录。'
            '近期记录仅提供所选范围内的上下文，不证明某条记录就是用户所说的那一次。'
            '无法确定范围时说明缺口，不提交猜测参数。'
            f'本次业务日期为 {business_today().isoformat()}，时区 Asia/Shanghai。'
        )),
        HumanMessage(content=json.dumps({'required_tool': tool_id, 'requests': queries}, ensure_ascii=False)),
    ], extra_body={'thinking': {'type': 'disabled'}})
    calls = getattr(response, 'tool_calls', None)
    if (getattr(response, 'invalid_tool_calls', None) or not calls or len(calls) != 1
            or calls[0].get('name') != tool.name):
        raise ValueError('required_read_argument_response_invalid')
    return validate_read_arguments(tool_id, tool, calls[0].get('args'), queries)
