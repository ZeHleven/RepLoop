"""Disambiguate a source-proven meal creation from saving a whole Artifact.

This never extracts food names, quantities or dates into business fields. The
normal change extractor and proposal confirmation remain authoritative.
"""
import re

from app.services.agent_intent import _is_artifact_save_request

_MEAL = r'(?:早餐|午餐|晚餐|加餐)'
_CREATE = r'(?:补记|记录(?!过|的|于)|记下|新增|添加)'
_PREFIX = r'(?:(?:请|帮我|麻烦|我想|我要|再|另外|然后|并|只)\s*)*'
_FRONT = re.compile(rf'^{_PREFIX}{_CREATE}.{{0,100}}?{_MEAL}')
_BACK = re.compile(rf'^{_PREFIX}(?:把|将).{{0,80}}?{_MEAL}.{{0,100}}?(?:记下来|记录下来|记下|记录为|记录成|存成待确认提案)')
_NONCURRENT = re.compile(r'如果|假设|假如|例如|比如|朋友|别人|(?:用户|[他她])说|[“”「」『』\"]')
_NEGATED = re.compile(r'不要|不用|不必|无需|不想|不需要|别|不记录|不保存')
_ARTIFACT_NOUN = re.compile(r'方案|菜单|计划')
_NEGATED_SAVE = re.compile(r'(?:不要|不用|不必|无需|不想|不需要|别|不希望)(?:(?:再|继续|先|现在|直接|马上)|(?:把|将)[^，,。；;只但]{0,24})*\s*(?:保存|记录|提交)')


def _positive_artifact_save(message):
    return any(_is_artifact_save_request(clause) and not _NEGATED_SAVE.search(clause)
               and not _NONCURRENT.search(clause) for clause in re.split(r'[，,。；;！？!?\n]',message))


def explicit_meal_creation(message: str) -> bool:
    # Negation and quoted/hypothetical scope survive before compound clauses
    # are split. A food quantity by itself is not a new write command.
    for clause in re.split(r'[，,。；;！？!?\n]',message):
        if _NONCURRENT.search(clause) or _NEGATED.search(clause):
            continue
        for part in re.split(r'(?=并(?:保存|记录|补记)|然后(?:保存|记录)|再(?:保存|记录))',clause):
            if not _ARTIFACT_NOUN.search(part) and (_FRONT.search(part.strip()) or _BACK.search(part.strip())):
                return True
    return False


def normalize_meal_artifact_target(route, message: str):
    if (route.intent_domain != 'nutrition' or route.request_kind != 'mutation'
            or route.requested_effect != 'create'):
        return route
    meal = explicit_meal_creation(message)
    artifact = _positive_artifact_save(message)
    # A single artifact shortcut cannot carry an independent meal creation.
    # Reject on both passes, including a repair that deletes artifact_action.
    if meal and artifact:
        raise ValueError('meal_and_artifact_targets_require_separate_requests')
    if not route.artifact_action:
        return route
    if _NONCURRENT.search(message):
        raise ValueError('artifact_save_requires_current_request')
    if meal:
        return route.model_copy(update={'artifact_action':None})
    # A negative/question statement about saving is not a card action. Keep
    # legitimate positive saves (including "不要确认，只保存方案") intact.
    if (not artifact and _NEGATED.search(message)) or re.search(r'保存了吗|是否.{0,10}保存|怎么.{0,10}保存|如何.{0,10}保存',message):
        raise ValueError('artifact_save_requires_current_request')
    return route
