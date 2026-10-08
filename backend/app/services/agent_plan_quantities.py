"""Finite, source-bound scalar quantities for fulfilling deferred plan input.

These spans are evidence for a typed model value, not instructions or defaults.
Ambiguous, negated and unsupported expressions supply no evidence.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

_DIGITS = dict(zip('零一二三四五六七八九', range(10))) | {'两': 2}
_CN = r'[零一二两三四五六七八九十百千]+(?:点[零一二三四五六七八九]+)?'
_NUMBER = rf'(?:\d+(?:\.\d+)?|{_CN}|半)'
_QUANTITY = re.compile(
    rf'(?<![\d.零一二两三四五六七八九十百千点负-])(?P<number>{_NUMBER})\s*'
    rf'(?P<unit>分钟|分|秒钟|秒|公斤|千克|kg|克|周|天|组|次)'
    rf'(?P<tail>半(?:钟)?|\s*{_NUMBER}\s*秒)?', re.I)
_FIELD_UNITS = {
    'schedule.duration_weeks': {'周': Decimal(1)},
    'schedule.days_per_week': {'天': Decimal(1)},
    'exercise.sets': {'组': Decimal(1)},
    'exercise.reps': {'次': Decimal(1)},
    'exercise.rest_seconds': {'分钟': Decimal(60), '分': Decimal(60), '秒钟': Decimal(1), '秒': Decimal(1)},
    'exercise.recommended_weight_kg': {'公斤': Decimal(1), '千克': Decimal(1), 'kg': Decimal(1), '克': Decimal('.001')},
}
_UNCERTAIN = re.compile(r'不|未|没|别|勿|待定|待补|稍后|以后|大约|大概|约|左右|上下|至少|至多|最多|最少|超过|不足|如果|假如|假设|可能|也许|原来|原本|旧计划|之前|上次|[?？]|吗')
_RANGE = re.compile(rf'{_NUMBER}\s*(?:分钟|秒|公斤|千克|kg|周|天|组|次)?\s*(?:到|至|或|~|～|－|-)\s*{_NUMBER}')


@dataclass(frozen=True)
class PlanQuantity:
    quote: str
    start: int
    end: int
    value: Decimal
    unit: str


def _number(text: str) -> Decimal | None:
    if text == '半':
        return Decimal('.5')
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return Decimal(text)
    whole, dot, fraction = text.partition('点')
    # Deliberately bounded standard Chinese integers (0..9999). No shorthand
    # such as 一百五, which can mean either 105 or 150 in conversation.
    digit = r'[一二两三四五六七八九]'
    tens = rf'(?:{digit}?十[一二三四五六七八九]?|{digit})'
    hundreds = rf'(?:{digit}百(?:零{digit}|{digit}十[一二三四五六七八九]?)?|{tens})'
    grammar = rf'(?:零|{digit}千(?:零(?:{tens})|{digit}百(?:零{digit}|{digit}十[一二三四五六七八九]?)?)?|{hundreds})'
    if not re.fullmatch(grammar, whole):
        return None
    result = 0; current = 0
    for char in whole:
        if char in _DIGITS:
            current = _DIGITS[char]
        else:
            result += (current or 1) * {'十': 10, '百': 100, '千': 1000}[char]
            current = 0
    value = Decimal(result + current)
    if dot:
        value += Decimal('0.' + ''.join(str(_DIGITS[c]) for c in fraction))
    return value


def plan_quantities(message: str) -> list[PlanQuantity]:
    result = []
    for match in _QUANTITY.finditer(message):
        value = _number(match['number']); unit = match['unit'].lower(); tail = (match['tail'] or '').strip()
        if value is None:
            continue
        if tail:
            if unit not in {'分钟', '分'}:
                continue
            seconds = Decimal(30) if tail.startswith('半') else _number(tail[:-1].strip())
            if seconds is None or seconds >= 60:
                continue
            value = value * 60 + seconds; unit = '秒'
        result.append(PlanQuantity(match.group(), match.start(), match.end(), value, unit))
    return result


def provided_plan_value(change, message: str, *, targets=()) -> bool:
    factors = _FIELD_UNITS.get(change.field_path, {})
    if isinstance(change.value, bool) or not factors:
        return False
    try:
        expected = Decimal(str(change.value))
    except (InvalidOperation, ValueError):
        return False
    if not expected.is_finite():
        return False
    target = change.target_reference
    known_targets = set(targets) | ({target} if target else set())
    # A sentence is a target scope; comma-separated parameter continuations
    # may inherit its target, but a named different exercise must not do so.
    for sentence in re.split(r'[。；;\n]', message):
        for quantity in plan_quantities(sentence):
            if quantity.unit not in factors or quantity.value * factors[quantity.unit] != expected:
                continue
            start = max(sentence.rfind(c, 0, quantity.start) for c in ('，', ',', '、')) + 1
            ends = [p for c in ('，', ',', '、') if (p := sentence.find(c, quantity.end)) >= 0]
            end = min(ends, default=len(sentence))
            clause = sentence[start:end]
            if _UNCERTAIN.search(clause) or _RANGE.search(clause):
                continue
            if target:
                before = sentence[:quantity.start]
                mentions = [(m.start(), name) for name in known_targets for m in re.finditer(re.escape(name), before)]
                if not mentions or max(mentions)[1] != target:
                    continue
                # If the target is in an earlier comma clause, only an
                # unambiguous parameter continuation may inherit it.
                prefix = sentence[start:quantity.start].strip()
                if target not in prefix and not re.fullmatch(r'(?:每组|组间|之间|休息|重量|用|为|改为|改成|设为|设置为|调成|调整为|组数|次数|目标|建议|恢复|还原|重设|原计划|的|\s)*', prefix):
                    continue
            return True
    return False
