"""Source-bound nutrition date operands, never intake or target completeness."""
import re
from datetime import date, timedelta

_DAY = r'(?:今天|今日|昨天|昨日|前天|\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日)'
_HEAD = re.compile(rf'^(?:(?:请|帮我|我)(?:再)?)?(?:核对|评估|评价|分析|查询|查看|看看)?(?P<day>{_DAY})(?:的)?(?=饮食|营养|三餐|早餐|午餐|晚餐|加餐|只记了|记了|虽然记了)')
_OTHER_TIME = re.compile(rf'{_DAY}|明天|后天|大后天|大前天|上周|本周|这周|下周|上月|本月|这个月|下月|去年|今年|明年|最近|近期|过去|长期|以前|此前|之后|之前|前一天|后一天|每天|每日|每周|(?:近|这|过去|前|后)?[零〇一二两三四五六七八九十百\d]+(?:个)?(?:自然日|天|日|周|月|年)|这几天|这些天|这段时间|其他日期|另一天|上次|其余记录')


def _assessment_selection(message, today):
    """Whole-clause assessment operands, including user target declarations.

    A daily target is a comparison operand, not a request for other dates.
    Unknown clauses (including date revisions) reject the whole proof.
    """
    from app.services.nutrition_query_scope import _SCOPE, _explicit_scope
    clauses = [m.group().strip() for m in re.finditer(r'[^，,。；;！？!?\n]+', message)]
    header = re.compile(rf'(?:请|帮我)?(?:评估|评价|分析)(?P<scope>{_SCOPE})(?:的)?(?:我的)?(?:饮食|营养|饮食记录)')
    complete = re.compile(rf'(?P<day>{_DAY})(?:的)?(?:全部|所有)(?:食物(?:和|及)?饮料|食物与饮料)(?:都)?(?:已|已经)?(?:记齐|记录齐全|记录完整)')
    target = r'(?:蛋白质|碳水(?:化合物)?|脂肪)\s*\d+(?:\.\d+)?\s*(?:克|g)'
    annotations = [
        r'(?:三大营养素|蛋白质和碳水和脂肪)(?:有没(?:有)?|是否)(?:达到|达标于?)(?:我的)?目标',
        r'(?:请|帮我)?(?:分别)?核对(?:已有|已保存|已记录)?饮食记录与(?:我|本人)的(?:每天|每日)目标[：:]?',
        rf'{target}(?:、{target})*',
        r'(?:再)?(?:给|给出)(?:我)?(?:些|一些|一条|两条|最多一条)?(?:具体)?(?:调整|搭配)?建议',
        r'(?:只给|只要|仅给)(?:各项)?差额',
        r'(?:别|不要|不用)(?:替我)?保存(?:任何东西|任何内容|数据)?',
    ]
    selected = [];declared = False;comparison = False
    for part in clauses:
        match = header.fullmatch(part)
        if match:
            try: value = _explicit_scope(match['scope'], today)
            except ValueError:return None
            if value is None:return None
            selected.append(value);continue
        match = complete.fullmatch(part)
        if match:
            raw = match['day']
            if raw in {'今天','今日','昨天','昨日','前天'}:
                day = today-timedelta(days={'今天':0,'今日':0,'昨天':1,'昨日':1,'前天':2}[raw])
            else:
                from app.services.nutrition_query_scope import _calendar_day
                try:day = _calendar_day(raw,today)
                except ValueError:return None
            selected.append({'start_date':day.isoformat(),'end_date':day.isoformat()});declared=True;continue
        # A colon may introduce the first numeric target in the same clause.
        parts = re.split(r'[：:]',part,maxsplit=1)
        if len(parts)==2 and re.fullmatch(annotations[1],parts[0]) and re.fullmatch(annotations[2],parts[1]):
            comparison=True;continue
        if re.fullmatch(annotations[1],part):comparison=True;continue
        if not any(re.fullmatch(pattern,part) for pattern in annotations):return None
    if len(selected)!=1 or declared and not comparison:return None
    return selected[0]


def requested_nutrition_dates(message: str, today: date):
    """Return all demonstrable requested dates plus exact source anchors.

    Explicit compound/report selections share the existing whole-request
    grammar. Single-day assessment requires a leading nutritional operand;
    an example, quote, negation, comparison or later time revision cannot
    silently become a narrower query. Unrecognised operands return None.
    """
    from app.services.nutrition_query_scope import explicit_requested_selection
    selection = explicit_requested_selection(message, today)
    if selection is None:
        selection = _assessment_selection(message, today)
    if selection is not None:
        try:
            if selection.get('logged_dates'):
                days = sorted({date.fromisoformat(d) for d in selection['logged_dates']})
            else:
                start, end = date.fromisoformat(selection['start_date']), date.fromisoformat(selection['end_date'])
                if not 0 <= (end-start).days < 366:
                    return None
                days = [start+timedelta(days=i) for i in range((end-start).days+1)]
        except (KeyError, TypeError, ValueError):
            return None
        if days and max(days) <= today:
            return tuple(d.isoformat() for d in days), (message,)
        return None
    text = message.strip()
    match = _HEAD.match(text)
    if not match:
        return None
    tail = text[match.end():]
    # Whole-clause exclusion of a long-term inference adds no date operand.
    tail = re.sub(r'(?:^|[，,。；;])(?:不要|别)(?:推测|推断|判断)(?:我的)?长期(?:饮食)?习惯[。；;]?(?=$)', '', tail)
    if _OTHER_TIME.search(tail) or re.search(r'比较|对比|趋势|相比|分别核对|直到|截至|以外|之外|前后|其他日期', tail):
        return None
    raw = match['day']
    if raw in {'今天', '今日', '昨天', '昨日', '前天'}:
        value = today-timedelta(days={'今天':0, '今日':0, '昨天':1, '昨日':1, '前天':2}[raw])
    else:
        from app.services.nutrition_query_scope import _calendar_day
        try:
            value = _calendar_day(raw, today)
        except ValueError:
            return None
    return ((value.isoformat(),), (match.group(),)) if value <= today else None


def nutrition_read_evidence(message, *, domain, kind, effect, risk, evidence):
    """Refine date-specific nutrition hints without erasing other subgoals."""
    from app.services.business_clock import business_today
    original = list(dict.fromkeys(evidence))
    if domain != 'nutrition' or kind not in {'query', 'assessment'} or effect != 'read' or risk == 'high':
        return original, [], []
    recent = recent_assessment_request(message)
    if recent is not None:
        # This tool is the existing default latest-14-logged-dates view, not
        # a calendar window. Only a fully consumed vague request can use it.
        managed = {'nutrition_today', 'nutrition_history', 'nutrition_recent_context'}
        required = ['nutrition_recent_context']+[e for e in original if e not in managed]
        optional = [e for e in original if e in managed and e != 'nutrition_recent_context']
        return required+optional, optional, [recent]
    scope = requested_nutrition_dates(message, business_today())
    if scope is None:
        return original, [], []
    dates, quotes = scope
    chosen = 'nutrition_today' if dates == (business_today().isoformat(),) else 'nutrition_history'
    managed = {'nutrition_today', 'nutrition_history', 'nutrition_recent_context'}
    required = list(dict.fromkeys([chosen]+[e for e in original if e not in managed]))
    optional = [e for e in original if e in managed and e != chosen]
    return required+optional, optional, list(quotes)


def recent_assessment_request(message):
    """A complete vague recent assessment, never a partial scope extraction."""
    if not isinstance(message,str) or len(message)>1200:return None
    parts=[s.strip() for s in re.split(r'[，,。；;！？!?\n]',message) if s.strip()]
    head=re.compile(r'(?:(?:请|帮我)?(?:评估|评价|分析|看看))?(?:我|我的)?'
                    r'(?:最近|近期|这几天|这些天)(?:的)?'
                    r'(?:饮食(?:记录|情况)?|营养(?:情况)?|吃得?(?:怎么样|如何|怎样))'
                    r'(?:吃得)?(?:怎么样|如何|怎样)?')
    extras=[r'(?:三大营养素|蛋白质(?:、|和|及)碳水(?:、|和|及)脂肪)(?:分别)?(?:是否|有没有)(?:达标|达到(?:我的)?目标)',
            r'(?:是否|需不需要|要不要)(?:需要)?调整',
            r'(?:再|并)?(?:请)?(?:给|给出|提供)(?:我)?(?:一些|些|具体|一条|两条|最多一条|最多两条)?(?:饮食)?(?:结构|搭配|调整)?建议',
            r'(?:不要|不用|无需|别)(?:给)?(?:任何|额外)?(?:饮食|调整|搭配)?建议',
            r'(?:不要|不用|别)(?:保存|修改)(?:任何东西|任何内容|记录)?']
    if not parts or not head.fullmatch(parts[0]):return None
    if any(not any(re.fullmatch(pattern,s) for pattern in extras) for s in parts[1:]):return None
    return message
