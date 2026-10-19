"""Source-grounded two-day comparisons, independent of model arithmetic."""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.schemas.nutrition_history import DateComparison, NutritionHistoryArguments
from app.services.nutrition_query_scope import _calendar_day

_DATE = re.compile(r'\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{1,2}日|今天|昨天|前天|明天|后天')
_METRICS = {'热量':'calories', '蛋白质':'protein_g', '蛋白':'protein_g', '碳水化合物':'carbs_g', '碳水':'carbs_g', '脂肪':'fat_g'}
_METRIC = re.compile('|'.join(_METRICS))
_RELATION = re.compile(r'差多少|相差|差值|差额|哪(?:一)?天|哪个日期|更多|更少|较多|较少|减去|减掉|相减|比.+(?:多|少)')
_NONCURRENT = re.compile(r'如果|假如|假设|假定|例如|比如|朋友|别人|同事|[他她]的|[“”「」『』\"‘’]')
_NEGATED = re.compile(r'不要|不用|不必|不需要|不要求|不想|无需|别|不比较|不对比')
_EXTRA_DELIVERY = re.compile(r'建议|推荐|搭配|调整|(?:是否|有没有|是不是|需要|够不够).*(?:达标|健康|合适)|达标|食品明细|食物明细')
_SPLIT = r'[，,。；;！？!?\n]'
_LABELS = {'calories':('热量','千卡','total_calories'), 'protein_g':('蛋白质','g','total_protein_g'),
           'carbs_g':('碳水','g','total_carbs_g'), 'fat_g':('脂肪','g','total_fat_g')}


def _dates(message, today):
    """Resolve only explicit day tokens; do not infer an unstated year rollover."""
    rows, previous = [], None
    for token in _DATE.finditer(message):
        quote = token[0]
        if quote in {'今天','昨天','前天','明天','后天'}:
            day = today + timedelta(days={'今天':0,'昨天':-1,'前天':-2,'明天':1,'后天':2}[quote])
        else:
            # A bare day needs a preceding calendar month, not the business month.
            if re.fullmatch(r'\d{1,2}日', quote) and previous is None:
                return []
            month = re.fullmatch(r'(\d{1,2})月\d{1,2}日', quote)
            if previous and month and {previous.month,int(month[1])} == {1,12}:
                return []
            try:
                day = _calendar_day(quote, today, previous)
            except ValueError:
                return []
        rows.append((quote, day.isoformat(), token.start(), token.end()))
        previous = day
    return rows


def _requirements(message):
    comparisons, listed = set(), set()
    for clause in _clauses(message):
        if _NONCURRENT.search(clause) or _NEGATED.search(clause):
            continue
        metrics = {_METRICS[m[0]] for m in _METRIC.finditer(clause)}
        if _RELATION.search(clause) and not re.search(r'最高|最低|极差',clause):
            comparisons |= metrics
        if re.search(r'列|各自|分别|逐日|每天|每日', clause):
            listed |= metrics
    return comparisons, listed


def _clauses(message):
    # Do not split "不要再比较" after its negation. Clause qualifiers must
    # survive before a smaller metric/relation span is interpreted.
    for clause in re.split(_SPLIT, message):
        if _NONCURRENT.search(clause) or _NEGATED.search(clause):
            yield clause
        else:
            yield from re.split(r'(?=然后|再(?:比较|对比|计算)|并(?:比较|对比|计算))',clause)


def grounded_date_comparisons(message, plan, today):
    rows = _dates(message, today)
    selected = {r[1] for r in rows}
    required, listed = _requirements(message)
    comparisons = plan.date_comparisons
    # This only guards proved two-day difference requests, not max/min/range
    # over arbitrary periods or existing single-day numeric target comparisons.
    if len(selected) != 2:
        return not comparisons
    if not comparisons:
        return not required
    if _NONCURRENT.search(message) or not required:
        return False
    if any(_EXTRA_DELIVERY.search(clause) and not _NEGATED.search(clause) for clause in re.split(_SPLIT,message)):
        return False
    # Calendar intervals are not a two-element selection, except an adjacent
    # two-day interval. Do not turn the endpoints of a week into two dates.
    for left,right in zip(rows,rows[1:]):
        between=message[left[3]:right[2]]
        if re.fullmatch(r'\s*(?:到|至|~|～|—|－)\s*',between):
            if (date.fromisoformat(right[1])-date.fromisoformat(left[1])).days != 1:
                return False
    if ({c.metric for c in comparisons} != required or not listed <= set(plan.metrics)
            or set(re.findall(r'早餐|午餐|晚餐|加餐', message)) != set(plan.meal_types)):
        return False
    if any({c.left_date,c.right_date} != selected for c in comparisons):
        return False
    for comp in comparisons:
        for quote, day in ((comp.left_date_quote,comp.left_date),(comp.right_date_quote,comp.right_date)):
            matches = [r for r in rows if r[0] == quote]
            if not matches or {r[1] for r in matches} != {day}:
                return False
        quote = comp.comparison_quote
        if (quote not in message or not _RELATION.search(quote)
                or comp.metric not in {_METRICS[m[0]] for m in _METRIC.finditer(quote)}):
            return False
        # An isolated positive-looking substring inside a negated clause is
        # not evidence. Match the complete containing clause as well.
        if not any(quote in clause and not _NEGATED.search(clause) and not _NONCURRENT.search(clause)
                   for clause in re.split(_SPLIT, message)):
            return False
    # Explicit subtraction has an order. Neutral "which day has more" allows
    # either orientation because the report spells out the signed formula.
    for left, right in zip(rows, rows[1:]):
        between = message[left[3]:right[2]]
        if re.search(r'减去|减掉|减|比', between) and not re.search(r'[。；;！？!?]', between):
            if any((c.left_date,c.right_date) != (left[1],right[1]) for c in comparisons):
                return False
    ordered = list(dict.fromkeys(r[1] for r in rows))
    for left, right in re.findall(r'(前者|后者)(?:的[^，。；]{0,12})?减去(前者|后者)',message):
        expected = (ordered[0 if left=='前者' else 1],ordered[0 if right=='前者' else 1])
        if any((c.left_date,c.right_date) != expected for c in comparisons):
            return False
    return True


def render_date_comparisons(data, comparisons):
    """Validate the observed scope before deriving values; missing is not zero."""
    failure = ['两日期比较：返回数据的范围或数值未通过校验，暂时无法可靠计算差值。']
    try:
        comps = [c if isinstance(c,DateComparison) else DateComparison.model_validate(c) for c in comparisons]
        scope, days = data['scope'], data['days']
        args = NutritionHistoryArguments(start_date=scope.get('start_date') or '',end_date=scope.get('end_date') or '',
                                         logged_dates=scope.get('logged_dates') or [],meal_types=scope.get('meal_types') or [])
        as_of = date.fromisoformat(scope['as_of'])
        if scope.get('selection') == 'specific_dates' and args.logged_dates:
            selected = set(args.logged_dates)
        elif (scope.get('selection') == 'calendar_range' and args.start_date
              and date.fromisoformat(args.end_date)-date.fromisoformat(args.start_date) == timedelta(days=1)):
            selected = {args.start_date,args.end_date}
        else:
            return failure
        by_date = {row['date']:row for row in days}
        if (len(selected) != 2 or len(by_date) != len(days) or scope.get('returned_dates') != len(days)
                or not set(by_date) <= selected or any(date.fromisoformat(d)>as_of for d in by_date)
                or any({c.left_date,c.right_date} != selected for c in comps)):
            return failure
        lines = ['两日期比较仅反映所选餐次的已记录量；不代表实际全天摄入，也不判断个人是否达标。']
        for comp in comps:
            label, unit, field = _LABELS[comp.metric]
            missing = [d for d in (comp.left_date,comp.right_date) if d not in by_date]
            if missing:
                lines.append(f"{label}：{'、'.join(missing)}缺少可比历史记录，无法计算两日差值；不能将缺记录当作0。")
                continue
            raw = [by_date[d][field] for d in (comp.left_date,comp.right_date)]
            if any(isinstance(v,bool) or not isinstance(v,(int,float,Decimal)) for v in raw):
                return failure
            left, right = (Decimal(str(v)) for v in raw)
            if any(not v.is_finite() or v < 0 for v in (left,right)):
                return failure
            delta = left-right
            fmt = lambda v: format(v.normalize() if v else Decimal(0),'f')
            verdict = ('两日已记录量相同' if delta == 0 else
                       f"{comp.left_date if delta > 0 else comp.right_date}已记录更多，差 {fmt(abs(delta))} {unit}")
            lines.append(f'{label}差值（{comp.left_date} − {comp.right_date}）：{fmt(left)} − {fmt(right)} = {fmt(delta)} {unit}；{verdict}。')
        return lines
    except (ValueError,TypeError,KeyError,InvalidOperation):
        return failure
