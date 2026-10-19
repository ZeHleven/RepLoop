"""Finite query operands; not date parsing, business facts, or write authority."""
import re

_DAY = r"(?:\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日)"
_END = rf"(?:{_DAY}|\d{{1,2}}日)"
_COUNT = r"[\d一二三四五六七八九十两]+"
_PERIOD = rf"(?:(?:最近|过去|近){_COUNT}(?:个)?(?:自然日|天)|本周(?:到|至)今天)"
_SCOPE = rf"(?:{_DAY}(?:(?:到|至|~|～){_END}|(?:和|及|、){_DAY})*|{_PERIOD}|(?:最近|近){_COUNT}个(?:有(?:饮食)?记录的?日期|记录日期))"
_MEALS = r"(?:早餐|午餐|晚餐|加餐)(?:(?:和|及|、)(?:早餐|午餐|晚餐|加餐))*"
_METRIC = r"(?:饮食)?(?:总)?(?:热量|蛋白质|碳水|脂肪)"
_DAILY = rf"(?:每日|每天|逐日)(?:的)?{_METRIC}"
_PREFIX = r"(?:仍然|仍|还是|同样|继续)?"
_KEEP = r"(?:保持(?:不变)?|保留|不变|照旧)"
_KEEP_FIELD = r"(?:(?:查询)?(?:日期|时间)(?:范围)?|(?:所有)?(?:查询|筛选|统计|输出)?(?:条件|要求|方式))"
_PRESERVATION = rf"(?:其他|其余)?{_KEEP_FIELD}(?:(?:和|及|与){_KEEP_FIELD})*(?:都)?{_KEEP}"
_RETURN_SELECTION = re.compile(r'^(?:回到|恢复到)(?:最开始|最初|原来)(?:那次|那一次)?[：:](?P<query>.+)$')


def is_nutrition_filter_revision(message: str) -> bool:
    # Consume every clause. A matching date edit cannot shelter a second
    # operation, stored record, hypothetical instruction, or negation.
    # Normalize formatting only inside a whole-clause, bounded grammar.
    message = re.sub(r"[ \t]+", "", message)
    returned = _RETURN_SELECTION.fullmatch(message)
    if returned:
        from app.services.business_clock import business_today
        # The new selection and all qualifiers must be stated explicitly.
        # The caller still requires a successful read and matching task ID.
        return _composed_history_goal(returned['query'], business_today()) is not None
    # Join a conditional annotation with its explanation, not arbitrary clauses.
    message = re.sub(r"((?:如果|若)(?:有)?日期(?:暂时)?(?:不能|无法|不)(?:计入|纳入|统计))[，,](?=(?:也)?(?:请)?(?:说明|列明))", r"\1", message)
    clauses = [part.strip() for part in re.split(r"[，,。；;！!？?]", message) if part.strip()]
    edit = rf"(?:把)?(?:(?:查询)?(?:日期|时间)(?:范围)?)?(?:改成|改为|调整为|更正为)(?P<scope>{_SCOPE})(?:(?:这|那)(?P<count>{_COUNT})天)?"
    # Bare meal edits can target stored meals. Require either an explicit
    # query field or a read-filter operator, in addition to verified context.
    meal_edit = (rf"(?:把)?(?:查询餐次(?:改为|改成|调整为)(?:只看|只查|只要)?"
                 rf"|餐次(?:改为|改成|调整为)(?:只看|只查|只要)){_MEALS}")
    remove_filter = rf"(?:取消|去掉){_MEALS}筛选"
    other = (
        rf"{_PREFIX}(?:只看|只查|只要|只保留){_MEALS}",
        rf"{_PREFIX}(?:查|查询){_SCOPE}",
        r"(?:查询)?(?:日期|时间)(?:范围)?(?:不变|保持不变)",
        rf"(?:列|列出|同样列){_DAILY}(?:(?:并|和|及)(?:计算)?合计)?",
        r"(?:再)?(?:算|计算)(?:热量)?合计",
    )
    # These are answer/selection qualifiers, never business actions. They
    # compose only with a real filter edit and the caller's verified read
    # context. Every clause must still match; trailing writes cannot hide.
    annotations = (
        _PRESERVATION,
        rf'(?:多|少)看{_COUNT}个',
        r"(?:如果|若)(?:有)?日期(?:暂时)?(?:不能|无法|不)(?:计入|纳入|统计)(?:也)?(?:请)?(?:说明|列明)(?:一下)?",
        rf"(?:没有|无|未找到)(?:{_MEALS})?记录的日期(?:也)?(?:要|请)?(?:明确)?(?:列出|列明|点明|说明)",
        r"(?:缺(?:少)?|未)记录日期(?:也)?(?:要|请)?(?:明确)?(?:列出|列明|点明|说明)",
        rf"(?:空结果|没有(?:{_MEALS})?记录)(?:时)?(?:也)?(?:请)?(?:不要|不|不能)(?:给出|报|计算)(?:零|0)(?:均值|平均值)",
        rf"(?:空结果|没有(?:{_MEALS})?记录)(?:时)?(?:也)?(?:请)?(?:不要|不|不能)(?:当作|视为|推断为)(?:零摄入|未进食)",
    )
    # A quantity suffix must agree with the selected calendar days.
    for part in clauses:
        match = re.fullmatch(edit, part)
        if match and match['count']:
            from datetime import date
            try:
                selection = _explicit_scope(match['scope'], date(2000, 1, 1))
            except ValueError:
                return False
            if selection is None:
                return False
            count = (len(selection['logged_dates']) if 'logged_dates' in selection else
                     (date.fromisoformat(selection['end_date'])-date.fromisoformat(selection['start_date'])).days+1)
            numbers = dict(zip('一二三四五六七八九十', range(1, 11))) | {'两': 2}
            stated = int(match['count']) if match['count'].isdigit() else numbers.get(match['count'])
            if stated != count:
                return False
    edits = (edit, meal_edit, remove_filter)
    return bool(clauses) and any(re.fullmatch(pattern, part) for part in clauses for pattern in edits) and all(
        any(re.fullmatch(pattern, part) for pattern in (*edits, *other, *annotations)) for part in clauses
    )


def _legacy_single_daily_history_goal(message: str) -> bool:
    # Prove one complete historical report in the original utterance. This
    # only removes redundant evidence; the planner still resolves operands.
    # Every extra clause must be an annotation of the same report.
    clauses = [part.strip() for part in re.split(r"[，,。；;！!？?]", message) if part.strip()]
    statistic = r"(?:记录)?(?:合计|总计|总和|均值|平均值)"
    goal = (rf"(?:查|查询|列出|查看)?{_SCOPE}(?:的)?(?:{_MEALS})?(?:的)?{_DAILY}"
            rf"(?:(?:和|及|并)(?:计算)?{statistic})?(?:是多少)?")
    annotations = (
        r"(?:今天算在内|包含今天|包括今天|含今天)",
        rf"(?:缺(?:少)?|没有|无|未找到)(?:{_MEALS})?记录的(?:历史)?日期(?:也)?(?:要|请)?(?:明确)?(?:列出|列明|点明|说明)",
        r"(?:缺(?:少)?|未)记录日期(?:也)?(?:要|请)?(?:明确)?(?:列出|列明|点明|说明)",
        r"未来日期(?:单独)?(?:(?:标明|说明))?(?:未|不)纳入历史查询",
        rf"(?:空结果|没有(?:{_MEALS})?记录)(?:时)?(?:也)?(?:请)?(?:不要|不|不能)(?:给出|报|计算)(?:零|0)(?:均值|平均值)",
        rf"(?:空结果|没有(?:{_MEALS})?记录)(?:时)?(?:也)?(?:请)?(?:不要|不|不能)(?:当作|视为|推断为)(?:零摄入|未进食)",
    )
    goals = [part for part in clauses if re.fullmatch(goal, part)]
    return len(goals) == 1 and all(
        part == goals[0] or any(re.fullmatch(pattern, part) for pattern in annotations)
        for part in clauses
    )


# The source recognizer describes operands, never business facts or authority.
# Explicit scope matching is shared by report proof and planner source checks.
_QUERY_LEAD = r"(?:(?:再|另外|另|然后)?(?:请|帮我|我)?(?:查询|查看|列出|只看|只查|只要|看|查))?"
_EDIT_LEAD = r"(?:把)?(?:(?:查询)?(?:日期|时间)(?:范围)?)?(?:改成|改为|调整为|更正为)"
_SCOPE_START = re.compile(rf"^(?:{_EDIT_LEAD}|{_QUERY_LEAD})(?P<scope>{_SCOPE})")
_CLOSED = r"(?:[（(]含(?:两端|首尾(?:日期)?)[）)])?"
_MEAL_SCOPE = rf"(?:全部餐次|所有餐次|{_MEALS})"
_METRICS = rf"{_METRIC}(?:(?:和|及|与|、){_METRIC})*"
_DAILY_VALUES = rf"(?:每日|每天|逐日|逐天)(?:列出|列|看)?(?:这(?:[一二三四两\d])?餐合计的)?(?:的)?{_METRICS}(?:(?:分别)?列(?:出来|出|一下))?"
_STATISTIC = r"(?:(?:记录)?(?:合计|总计|总和|总量|均值|平均值|最高|最低|极差)|按有记录天数(?:算|计算)的日均)"
_AGGREGATE = rf"(?:并|再)?(?:分别)?(?:给出|给|计算|算)?(?:各项|四项)?{_STATISTIC}(?:(?:和|及|与|、){_STATISTIC})*"
_STATED_DAYS = rf'(?:(?:这|那)(?P<stated_days>{_COUNT})天)?'
_SOURCE_MEALS = r"(?:早餐|午餐|晚餐|加餐)(?:(?:和|及|与|、|加)(?:早餐|午餐|晚餐|加餐))*"
_SOURCE_TAIL = re.compile(
    rf"{_CLOSED}{_STATED_DAYS}(?:的)?(?:(?:全部餐次|所有餐次|{_SOURCE_MEALS}|饮食(?:历史|记录)?|营养(?:历史|记录)?)(?:的)?)?"
    rf"(?:{_DAILY_VALUES}|{_METRICS})?(?:(?:并|和|及)(?:计算)?{_STATISTIC})?(?:是多少)?"
)


def _calendar_day(value, as_of, previous=None):
    from datetime import date
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    match = re.fullmatch(r"(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日", value)
    if not match:
        raise ValueError("not a bounded calendar day")
    year, month, day = match.groups()
    anchor = previous or as_of
    return date(int(year or anchor.year), int(month or anchor.month), int(day))


def _explicit_scope(scope, as_of):
    """Only unambiguous explicit calendar selections; other scopes stay typed."""
    if not re.match(_DAY, scope):
        return None
    tokens = list(re.finditer(rf"{_DAY}|\d{{1,2}}日", scope))
    if not tokens:
        return None
    days, previous = [], None
    for token in tokens:
        previous = _calendar_day(token.group(), as_of, previous)
        days.append(previous.isoformat())
    connectors = [scope[left.end():right.start()] for left, right in zip(tokens, tokens[1:])]
    if len(days) == 2 and connectors[0] in {"至", "到", "~", "～"}:
        from datetime import date
        # A shorthand end can belong to the next year/month. Do not harden
        # our same-year default into a false constraint; leave it to planning.
        if not 0 <= (date.fromisoformat(days[1]) - date.fromisoformat(days[0])).days < 366:
            return None
        return {"start_date": days[0], "end_date": days[1]}
    if len(days) == 1:
        return {"start_date": days[0], "end_date": days[0]}
    if all(connector in {"和", "及", "、"} for connector in connectors):
        return {"logged_dates": days}
    return None


def _narrative_history_selection(message, as_of):
    """One fully sourced food-detail query plus optional generic advice.

    Do not extract date fragments from unknown modifiers, revisions, quoted
    examples or second goals. This constrains a plan, never supplies one.
    """
    clauses = [c.strip() for c in re.split(r'[，,。；;！!？?]', message) if c.strip()]
    goal = re.compile(
        rf'(?:请|帮我)?(?:核对|查询|查看|列出|只查)(?P<scope>{_SCOPE})'
        rf'(?:的)?(?P<meals>{_SOURCE_MEALS})?(?:的)?'
        rf'(?:各(?:种|项)?食物|食品|食物)(?:明细|分项)?(?:的)?{_METRICS}'
        r'(?:分项|明细)?'
    )
    advice = re.compile(r'(?:再|并|然后)?(?:请)?(?:给|给出)(?:我)?(?:一条|一些|一般)?(?:搭配)?建议')
    matches = [goal.fullmatch(c) for c in clauses]
    selected = [m for m in matches if m]
    if len(selected) != 1 or not all(m or advice.fullmatch(c) for c, m in zip(clauses, matches)):
        return None
    match = selected[0]
    try:
        selection = _explicit_scope(match['scope'], as_of)
    except ValueError:
        return None
    if selection is None:
        return None
    if match['meals']:
        selection['meal_types'] = re.findall(r'早餐|午餐|晚餐|加餐', match['meals'])
    return selection


def explicit_requested_selection(message, as_of):
    """Read source dates only when exactly one recognized selection is present.

    Clock declarations and future-exclusion references are not query selectors.
    Values are checked, never installed into a model plan by this function.
    """
    # A local date fragment is insufficient: a later, unrecognized clause
    # may revise it. Only complete report/verified-filter syntax can supply
    # a hard constraint; unknown language remains the planner's job.
    message = re.sub(r'[ \t]+','',message)
    returned = _RETURN_SELECTION.fullmatch(message)
    if returned and _composed_history_goal(returned['query'],as_of) is not None:
        message = returned['query']
    narrative = _narrative_history_selection(message, as_of)
    if narrative is not None:
        return narrative
    if not (is_nutrition_filter_revision(message)
            or _legacy_single_daily_history_goal(message)
            or _composed_history_goal(message, as_of, require_daily_metrics=False)):
        return None
    selections = []
    for clause in re.split(r"[，,。；;！!？?]", message):
        clause = clause.strip()
        match = _SCOPE_START.match(clause)
        if not match:
            continue
        tail = clause[match.end():]
        # Consume the entire operand's suffix. A leading 的 alone cannot
        # prove Oct 8 in "Oct 8 的前一天"; unknown modifiers stay with planning.
        if not _SOURCE_TAIL.fullmatch(tail):
            continue
        try:
            selection = _explicit_scope(match["scope"], as_of)
        except ValueError:
            return None
        if selection is not None:
            selections.append(selection)
    return selections[0] if len(selections) == 1 else None


def _composed_history_goal(message, as_of, *, require_daily_metrics=True):
    """Prove one report from complete, source-positioned clause roles."""
    message = re.sub(r"[ \t]+", "", message)
    message = re.sub(r"((?:如果|若)(?:有)?并列或缺(?:少)?记录)[，,](?=(?:也)?(?:一并)?说明)", r"\1", message)
    clauses = list(re.finditer(r"[^，,。；;！!？?]+", message))
    header = re.compile(rf"{_QUERY_LEAD}(?P<scope>{_SCOPE}){_CLOSED}{_STATED_DAYS}(?:的)?(?:{_MEAL_SCOPE})?")
    comparison = re.compile(rf"(?:请|帮我)?比较(?P<scope>{_SCOPE})(?:(?:这|那)(?P<count>{_COUNT})天)?(?:的)?整天饮食")
    values = _DAILY_VALUES if require_daily_metrics else rf"(?:{_DAILY_VALUES}|{_METRICS})"
    combined = re.compile(
        rf"{_QUERY_LEAD}(?P<scope>{_SCOPE}){_CLOSED}{_STATED_DAYS}(?:的)?(?:{_MEAL_SCOPE})?(?:的)?"
        rf"{values}(?:(?:并|和|及)(?:计算)?{_STATISTIC})?(?:是多少)?"
    )
    missing = (
        rf"(?:哪天|哪些日期|过去的日期)?(?:若)?(?:缺(?:少)?|没有|无|未找到)"
        rf"(?:这[一二三四两\d]?餐|所选餐次|{_MEALS})?(?:的)?记录"
        r"(?:的(?:历史)?日期)?(?:也)?(?:要|请)?(?:明确)?(?:列出|列明|点明|说明)"
    )
    annotations = (
        _PRESERVATION,
        r'不限餐次',
        r'(?:没(?:有)?|缺(?:少)?)记录或(?:还|暂时)?(?:不能|无法)纳入的日期(?:也)?(?:请)?说明',
        r"(?:如果|若)(?:有)?并列或缺(?:少)?记录(?:也)?(?:一并)?说明",
        missing, r"(?:缺(?:少)?|未)记录日期(?:也)?(?:要|请)?(?:明确)?(?:列出|列明|点明|说明)",
        r"(?:今天算在内|包含今天|包括今天|含今天)",
        r"未来日期(?:单独)?(?:(?:标明|说明))?(?:未|不)纳入历史查询",
        rf"(?:空结果|没有(?:{_MEALS})?记录)(?:时)?(?:也)?(?:请)?(?:不要|不|不能)(?:给出|报|计算)(?:零|0)(?:均值|平均值)",
        rf"(?:空结果|没有(?:{_MEALS})?记录)(?:时)?(?:也)?(?:请)?(?:不要|不|不能)(?:当作|视为|推断为)(?:零摄入|未进食)",
    )
    scope_count = metric_count = 0
    scope_value = None
    future_days = []
    has_exclusion = False
    roles = []
    for token in clauses:
        part = token.group().strip()
        if not part:
            continue
        kind = None
        match = combined.fullmatch(part)
        if match:
            if not _stated_days_match(match,as_of):return None
            scope_count += 1
            metric_count += 1
            scope_value = match["scope"]
            kind = "scope_and_daily_metrics"
        elif (match := header.fullmatch(part)):
            if not _stated_days_match(match,as_of):return None
            scope_count += 1
            scope_value = match["scope"]
            kind = "scope"
        elif (match := comparison.fullmatch(part)):
            try:
                selection = _explicit_scope(match['scope'], as_of)
            except ValueError:
                return None
            if not selection:
                return None
            if match['count']:
                from datetime import date
                count = (len(set(selection['logged_dates'])) if 'logged_dates' in selection else
                         (date.fromisoformat(selection['end_date'])-date.fromisoformat(selection['start_date'])).days+1)
                numbers = dict(zip('一二三四五六七八九十', range(1, 11))) | {'两': 2}
                stated = int(match['count']) if match['count'].isdigit() else numbers.get(match['count'])
                if stated != count:
                    return None
            scope_count += 1
            scope_value = match['scope']
            kind = 'comparison_scope'
        elif re.fullmatch(rf"(?:先列|先列出)?{values}", part):
            metric_count += 1
            kind = "daily_metrics"
        elif (re.fullmatch(_AGGREGATE, part) or re.fullmatch(
                r"再分别给出每一项最高和最低的数值与日期", part)):
            kind = "aggregation"
        elif any(re.fullmatch(pattern, part) for pattern in annotations):
            kind = "report_annotation"
        elif (match := re.fullmatch(rf"今天(?:按|是|算作)(?P<day>{_DAY})(?:算)?", part)):
            try:
                if _calendar_day(match["day"], as_of) != as_of:
                    return None
            except ValueError:
                return None
            kind = "business_clock"
        elif (match := re.fullmatch(rf"(?P<day>{_DAY})(?:属于|是)未来日期", part)):
            try:
                future_days.append(_calendar_day(match["day"], as_of))
            except ValueError:
                return None
            kind = "future_reference"
        elif re.fullmatch(r"(?:请)?(?:排除|剔除)(?:并说明)?", part):
            has_exclusion = True
            kind = "future_exclusion"
        else:
            return None
        roles.append({"kind": kind, "text": part, "start": token.start(), "end": token.end()})
    if scope_count != 1 or metric_count > 1 or (require_daily_metrics and metric_count != 1):
        return None
    if future_days:
        try:
            selection = _explicit_scope(scope_value, as_of)
        except ValueError:
            return None
        if not selection or not has_exclusion:
            return None
        from datetime import date
        for day in future_days:
            in_scope = (
                day.isoformat() in selection["logged_dates"] if "logged_dates" in selection
                else date.fromisoformat(selection["start_date"]) <= day <= date.fromisoformat(selection["end_date"])
            )
            if day <= as_of or not in_scope:
                return None
    elif has_exclusion:
        return None
    return {"scope": scope_value, "clauses": roles}


def _stated_days_match(match,as_of):
    count=match.groupdict().get('stated_days')
    if not count:return True
    from datetime import date
    try:
        selection=_explicit_scope(match['scope'],as_of)
        if not selection:return False
        actual=(len(set(selection['logged_dates'])) if 'logged_dates' in selection else
                (date.fromisoformat(selection['end_date'])-date.fromisoformat(selection['start_date'])).days+1)
        numbers=dict(zip('一二三四五六七八九十',range(1,11)))|{'两':2}
        return actual==(int(count) if count.isdigit() else numbers.get(count))
    except ValueError:return False


def is_single_daily_history_goal(message: str, *, as_of=None) -> bool:
    if _legacy_single_daily_history_goal(message):
        return True
    if as_of is None:
        from app.services.business_clock import business_today
        as_of = business_today()
    return _composed_history_goal(message, as_of) is not None

