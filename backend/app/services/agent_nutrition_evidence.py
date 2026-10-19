"""Prove equivalent recorded nutrition scope from actual tool observations.

This does not certify actual intake, personal adequacy or final answer quality.
Missing dates in a recent-date window are not evidence of no stored records.
"""
import json
import math
from langchain_core.messages import ToolMessage
from app.schemas.meal import DailySummary
from app.schemas.agent_trace import AgentNutritionEvidenceSubstitutionTrace
from app.services.agent_trace import observation_fingerprint
from app.services.agent_nutrition_scope import requested_nutrition_dates
from app.services.business_clock import business_today

_TOOLS = {'nutrition_get_recent_context':'nutrition.get_recent_context',
          'nutrition_get_today':'nutrition.get_today', 'nutrition_list_history':'nutrition.list_history'}
_NUMBERS = (('total_calories','calories'),('total_protein_g','protein_g'),
            ('total_carbs_g','carbs_g'),('total_fat_g','fat_g'))


def _complete_day(raw):
    summary = DailySummary.model_validate(raw)
    meals = summary.meals
    if (len({m.id for m in meals}) != len(meals)
            or any(not m.id or m.logged_at != summary.date or m.meal_type not in {'早餐','午餐','晚餐','加餐'} for m in meals)
            or len({m.user_id for m in meals}) > 1):
        return False
    items = [i for m in meals for i in m.items]
    if len({i.id for i in items}) != len(items) or any(not i.id or i.meal_id != m.id for m in meals for i in m.items):
        return False
    if any(not math.isfinite(i.amount_g) or i.amount_g <= 0 for i in items):
        return False
    for total, item_key in _NUMBERS:
        values = [getattr(i,item_key) for i in items]
        observed = getattr(summary,total)
        if (not math.isfinite(observed) or observed < 0
                or any(not math.isfinite(v) or v < 0 for v in values)
                or abs(observed-round(sum(values),1)) > 0.00001):
            return False
    return True


def _dated_records(data, tool, args, today):
    limits = data.get('evidence_limits', {})
    if (limits.get('intake_basis') != 'logged_items_only' or limits.get('whole_day_completeness') != 'unknown'
            or limits.get('unlogged_intake_is_zero') is not False):
        return None
    if tool == 'nutrition.get_today':
        if args or data.get('date') != today or data.get('count') != len(data['meals']):
            return None
        days = [data]
    else:
        scope = data['scope']
        if scope['as_of'] != today or scope['timezone'] != 'Asia/Shanghai' or scope['meal_types']:
            return None
        if tool == 'nutrition.get_recent_context':
            days = data['recent_days']
            if (args or scope['selection'] != 'latest_logged_dates' or scope['requested_limit'] != 14
                    or len(days) > 14 or scope['start_date'] or scope['end_date'] or scope['logged_dates']):
                return None
        else:
            from app.schemas.nutrition_history import NutritionHistoryArguments
            actual = NutritionHistoryArguments.model_validate(args)
            days = data['days']
            if (data['count'] != len(days) or actual.meal_types
                    or scope['start_date'] != (actual.start_date or None)
                    or scope['end_date'] != (actual.end_date or None)
                    or scope['logged_dates'] != actual.logged_dates
                    or scope['requested_limit'] != (None if actual.start_date or actual.logged_dates else actual.days)):
                return None
        dates = [d['date'] for d in days]
        if (scope['returned_dates'] != len(days) or len(set(dates)) != len(dates)
                or scope['first_logged_date'] != min(dates, default=None)
                or scope['last_logged_date'] != max(dates, default=None)):
            return None
        if tool == 'nutrition.get_recent_context':
            current = data['today']
            if current['date'] != today or not _complete_day(current):
                return None
            existing = next((d for d in days if d['date'] == today), None)
            if existing is not None and existing != current:
                return None
            days = days if existing is not None else days+[current]
    if any(d['date'] > today or not _complete_day(d) for d in days):
        return None
    return {d['date']:d for d in days}


def nutrition_substitutions(trace, result, query):
    contract = trace.evidence_contract
    today = business_today()
    if (not contract or contract.status == 'blocked' or trace.execution_mode != 'direct'
            or trace.terminal_action != 'answer' or result.get('read_error_code')
            or not trace.request_context or trace.request_context.as_of != today.isoformat()):
        return []
    scope = requested_nutrition_dates(query, today)
    if scope is None:
        return []
    dates, quotes = scope
    # A named today obligation can only be replaced by actual today data.
    needed = [t for t in contract.required_tools if t in {'nutrition.list_history','nutrition.get_today'}
              and not any(o.tool_id == t and o.status == 'success' for o in trace.observations)]
    proofs = []
    for message in result.get('messages', []):
        if not isinstance(message, ToolMessage) or message.status != 'success' or message.name not in _TOOLS:
            continue
        tool = _TOOLS[message.name]
        observations = [o for o in trace.observations if o.call_id == message.tool_call_id and o.tool_id == tool and o.status == 'success']
        actions = [a for a in trace.actions if a.call_id == message.tool_call_id and a.tool_id == tool and a.status == 'completed']
        fingerprint = observation_fingerprint(message.content)
        if len(observations) != 1 or len(actions) != 1 or observations[0].result_fingerprint != fingerprint:
            continue
        try:
            records = _dated_records(json.loads(message.content), tool, actions[0].arguments, today.isoformat())
            if records is None or not set(dates) <= records.keys():
                continue
            for required in needed:
                if required == tool or any(p.required_tool == required for p in proofs):
                    continue
                if required == 'nutrition.get_today' and dates != (today.isoformat(),):
                    continue
                proofs.append(AgentNutritionEvidenceSubstitutionTrace(
                    required_tool=required, observed_tool=tool, call_id=message.tool_call_id,
                    result_fingerprint=fingerprint, requested_dates=list(dates), as_of=today.isoformat(),
                    scope_quotes=list(quotes),
                ))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return proofs
