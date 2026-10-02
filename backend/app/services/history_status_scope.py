"""Finite workout status contract shared by reports, tools and SQL."""
from dataclasses import dataclass
import re
from typing import Literal

HistoryStatus = Literal['completed', 'in_progress', 'ended_early', 'skipped', 'abandoned']
STATUS_LABELS = {'completed':'已完成', 'in_progress':'进行中', 'ended_early':'提前结束', 'skipped':'跳过', 'abandoned':'放弃'}
DEFAULT_STATUSES = ('completed', 'ended_early')
_TERMS = re.compile(r'已(?:经)?完成|进行中|正在进行|提前结束|跳过|放弃')
_VALUES = {'已完成':'completed','已经完成':'completed','进行中':'in_progress','正在进行':'in_progress',
           '提前结束':'ended_early','跳过':'skipped','放弃':'abandoned'}

@dataclass(frozen=True)
class HistoryStatusScope:
    statuses: tuple[str, ...] | None = None
    complete: bool = True
    included: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()

@dataclass(frozen=True)
class StatusOperation:
    kind: str
    values: frozenset[str]
    quote: str
    operand: str = 'states'


_EXCLUSION_VERB = r'(?:不(?:再)?(?:包含|包括|含|看|查(?:询|看)?|是|列(?:出)?|显示|统计|算|计入)|排除|剔除|去掉|不要(?:再)?(?:看|查(?:询|看)?|统计|列出|显示|列)?|除去)'
_PREFIX_EXCLUSION = re.compile(_EXCLUSION_VERB + r'\s*(?:状态(?:为|是)?\s*)?$')
_POSTFIX_EXCLUSION = re.compile(r'^\s*(?:的)?(?:场次|训练|记录)?(?:都|也|先|暂时|暂|全部|一律)?\s*' + _EXCLUSION_VERB)
_UNCERTAIN_OPERATOR = re.compile(r'不|未|没|别|勿|可能|也许|如果|并非')
_REPLACE = re.compile(r'(?:改为|改成|换成|改看|改查)\s*(?:只|仅)?(?:查|看|包含|包括|显示|列出|保留)?\s*(?:状态(?:为|是)?)?\s*$')
_OTHERS = r'(?:(?:其他|其余)(?:的)?(?:全部|所有)?状态|(?:其他|其余|除此之外)(?=(?:都|全部|一律)?(?:要|保留|排除|不要)))'
_ALL = r'(?:全部|所有)(?:的)?状态'
_GROUP = re.compile(rf'(?:{_TERMS.pattern})(?:\s*(?:和|或|及|、|与)\s*(?:{_TERMS.pattern}))*|{_OTHERS}|{_ALL}')


def _status_operations(query: str) -> list[StatusOperation] | None:
    operations = []
    for clause in re.split(r'[，,。；;\n（）()]|但是|但', query):
        matches = list(_GROUP.finditer(clause)); previous_end = 0
        for index, match in enumerate(matches):
            prefix = clause[previous_end:match.start()]
            # With no separator, a later operand owns the intervening verb:
            # 只看已完成不看跳过 must not exclude both states.
            suffix = clause[match.end():] if index == len(matches) - 1 else ''
            negative_prefix = _PREFIX_EXCLUSION.search(prefix)
            negative_suffix = _POSTFIX_EXCLUSION.search(suffix)
            except_prefix = re.search(r'除(?:了|去)?\s*$', prefix)
            prefix_operator = negative_prefix or except_prefix
            residue = prefix[:prefix_operator.start()] if prefix_operator else prefix
            suffix_residue = suffix[negative_suffix.end():] if negative_suffix else suffix
            if _UNCERTAIN_OPERATOR.search(residue) or _UNCERTAIN_OPERATOR.search(suffix_residue):
                return None
            # "不要排除" is an unknown/double negation, not a removal.
            if negative_suffix and re.search(r'排除|剔除|去掉', suffix_residue):
                return None
            values = frozenset(_VALUES[m.group()] for m in _TERMS.finditer(match.group()))
            operand = 'states' if values else 'all' if re.fullmatch(_ALL, match.group()) else 'others'
            kind = 'exclude' if negative_prefix or negative_suffix else 'except' if except_prefix else 'replace' if _REPLACE.search(prefix) else 'include'
            operations.append(StatusOperation(kind, values, clause.strip(), operand))
            previous_end = match.end()
    return operations


def parse_history_status_scope(query: str) -> HistoryStatusScope:
    """Parse sourced set operations, then compute one effective status set."""
    for left, right in [('“', '”'), ('‘', '’'), ('"', '"'), ("'", "'")]:
        query = re.sub(re.escape(left) + '(' + _TERMS.pattern + ')' + re.escape(right), r'\1', query)
    explained = re.sub(r'[（(]未完成[）)]', '', query)
    if explained != query:
        scope = parse_history_status_scope(explained)
        return scope if scope.complete and scope.statuses and 'completed' not in scope.statuses else HistoryStatusScope(complete=False)
    if re.search(r'未完成|没(?:有)?完成|没练完|某(?:个|些)状态|不是不|并非不|不(?:再)?(?:排除|剔除)', query):
        return HistoryStatusScope(complete=False)
    operations = _status_operations(query)
    if operations is None:
        return HistoryStatusScope(complete=False)
    included = set(); excluded = set(); universe = False
    others = None
    reject_others = any(op.operand == 'others' and op.kind == 'exclude' for op in operations)
    for op in operations:
        if op.operand == 'others':
            if others is not None and others != op.kind:
                return HistoryStatusScope(complete=False)
            others = op.kind
            continue
        if op.operand == 'all':
            if op.kind != 'include':
                return HistoryStatusScope(complete=False)
            universe = True
            continue
        kind = ('include' if reject_others else 'exclude') if op.kind == 'except' else op.kind
        if kind == 'replace':
            included.clear(); excluded.clear(); universe = False; others = None
        (excluded if kind == 'exclude' else included).update(op.values)
    if included & excluded:
        return HistoryStatusScope(complete=False)
    if others == 'exclude':
        if not included:
            return HistoryStatusScope(complete=False)
        excluded.update(set(STATUS_LABELS) - included)
    elif others is not None:
        # "其他" needs a concrete exclusion anchor; otherwise its reference
        # is unknown (or contradicts an explicit only-selection).
        if not excluded:
            return HistoryStatusScope(complete=False)
        universe = True
    if universe or not included and excluded:
        included = set(STATUS_LABELS)
    if not included and not excluded:
        return HistoryStatusScope()
    result = tuple(s for s in STATUS_LABELS if s in included and s not in excluded)
    return HistoryStatusScope(result or None, bool(result), tuple(s for s in STATUS_LABELS if s in included), tuple(s for s in STATUS_LABELS if s in excluded))

def effective_history_statuses(statuses=None, *, completed_only=False):
    if statuses:
        if (len(statuses)!=len(set(statuses)) or any(s not in STATUS_LABELS for s in statuses)
                or completed_only and set(statuses)!={'completed'}):
            raise ValueError('invalid or conflicting history status filter')
        return tuple(s for s in STATUS_LABELS if s in statuses)
    return ('completed',) if completed_only else DEFAULT_STATUSES

def history_status_problem(resolution, user_message):
    """Check explicit user filters before any read execution path can run."""
    if (resolution.risk_level!='low' or resolution.intent_domain!='workout_history' or resolution.request_kind!='query'
            or resolution.requested_effect!='read' or resolution.change_requests):
        return False
    original=parse_history_status_scope(user_message)
    resolved=parse_history_status_scope(resolution.resolved_query)
    return (not original.complete or not resolved.complete
            or original.statuses is not None and original.statuses!=resolved.statuses)


def is_status_slot_answer(message):
    """Only a finite status selection, never a new query or write command."""
    remaining = _TERMS.sub('', message)
    remaining = re.sub(r'不包含|不包括|不含|排除|我选|选择|只看|只查|仅看|就看|只要|仅|只|和|或|及|与|的|场次|状态|训练', '', remaining)
    return not remaining.strip(' ，,。；;、!！')


def is_history_scope_revision(message: str) -> bool:
    """Recognize a complete finite filter edit, never a business write clause."""
    if not re.search(r"查询|显示|列出|筛选|日期|范围|状态", message):
        return False
    remaining = re.sub(r"\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{1,2}日", "", message)
    remaining = _TERMS.sub('', remaining)
    remaining = re.sub(r"不再包含|不列出|不显示|不包含|不包括|排除|不看|不查", "", remaining)
    remaining = re.sub(r"的记录|的训练|场次|同一|相同|同样|刚选的|两种|之前的|日期|时间|范围|区间|状态|条件|查询|显示|列出|筛选|改为|改成|换成|收窄到|缩小到|保留|不变|仍旧|仍然|仍|只要|只有|只|仅|到|至|与|和|或|及|都|的", "", remaining)
    return not remaining.strip(' ，,。；;、!！')
