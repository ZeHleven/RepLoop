"""Bounded continuation of server-verified read state, never arbitrary chat text."""
import re

from app.services.agent_intent import IntentResolution, resolve_intent
from app.services.workout_reporting import progress_request, report_today


_TIME = r'上上周|上周|前一周|本周|这周|(?:最近|近|过去)[\d一二三四五六七八九十两]+(?:个)?周'


def discard_stale_read_clarification(message: str, pending: dict | None) -> dict | None:
    if not pending or pending.get('request_kind', 'query') not in {'query', 'assessment'}:
        return pending
    direct = resolve_intent(message)
    if direct.primary_intent != 'general_qa' or direct.risk_level != 'low':
        return None
    if re.search(r'先不|不[查看比]|换个|改[查问]|取消', message) and len(message) > 8:
        return None
    return pending


def verified_query_context(item: dict) -> dict | None:
    context = item.get('query_context')
    if (item.get('role') != 'assistant' or not isinstance(context, dict)
            or context.get('successful_query') is not True
            or not context.get('source_run_id')
            or context.get('primary_intent') != 'workout_progress_query'
            or context.get('request_kind') not in {'query', 'assessment'}
            or context.get('requested_effect') != 'read' or context.get('risk_level') != 'low'):
        return None
    query = context.get('resolved_query')
    return context if isinstance(query, str) and 0 < len(query) <= 4000 else None


def resume_verified_query(message: str, history: list[dict] | None) -> IntentResolution | None:
    match = re.fullmatch(rf'\s*(?:那|那么)?\s*({_TIME})\s*(?:呢)?[？?。！!\s]*', message)
    if not match or not history:
        return None
    # Do not reach past a new topic, a failed read, or an unanswered question.
    context = verified_query_context(history[-1])
    if context is None:
        return None
    query = context['resolved_query']
    # Comparisons and explicit date intervals have multiple scopes. Ask the
    # semantic model rather than guessing which operand the user wants changed.
    if any(word in query for word in ('比较', '对比', '相比', '同比', '环比')) or len(re.findall(_TIME, query)) != 1:
        return None
    query = re.sub(_TIME, match[1], query)
    if progress_request(query, report_today()) is None:
        return None
    return IntentResolution(
        primary_intent='workout_progress_query', intent_domain='workout_progress',
        request_kind='query', requested_effect='read', risk_level='low',
        evidence_requirements=['workout_progress'], resolved_query=query,
        subtasks=[query], confidence=0.98,
    )
