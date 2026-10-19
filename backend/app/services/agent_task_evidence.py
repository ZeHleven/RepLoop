"""Source-bound read obligations, separate from model-selected read hints.

This policy only refines supported read tasks. It never parses write values or
authorizes a write, and unknown compound goals keep their original obligations.
"""
import re

_WORKOUT = {'workout_plan', 'workout_history', 'workout_progress', 'workout_session'}
_MANAGED = {'active_plan', 'profile_summary', 'health_screening', 'workout_history',
            'workout_progress', 'workout_daily_context', 'next_workout', 'active_workout_session'}
_TEMPORARY = re.compile(r'(?:临时|过渡|减量|保底)(?:训练)?(?:安排|建议|调整|方案|规划)')
_SAVED_PLAN_REQUEST = re.compile(r'(?:结合|参考|按照?|基于)(?:我(?:的)?)?(?:已存|已保存|生效|当前)(?:的)?(?:训练)?计划')
_PLAN_DAYS = re.compile(r'计划(?:中|里|显示|的|已|完成)*(?:训练日|训练天数|完成日)|计划[^。；;\n]{0,10}完成(?:(?:训练)?日|(?:几|多少)天)')
_HISTORY_COUNT = re.compile(r'训练次数|训练场次|(?:历史)?已完成(?:训练)?场次|(?:已)?完成(?:了)?几次训练|实际记录|训练记录')
_NEGATIVE_REQUEST = re.compile(r'(?:不要|先别|(?<![分区识鉴辨告差级性])别|无需|不用|不必)\s*(?:再)?(?:核对|查询|统计|解释|列出|看|评估|判断|提供|给出|给)')
_EXCLUDED_METRIC = re.compile(
    r'(?:请|也|并|先|暂时|暂|且)?(?:不要|不用|无需|不必|不)(?:据此)?'
    r'(?:判断|推断|评估|分析|计算|比较)(?:训练)?'
    r'(?:强度|容量|负重|重量|组数|重复次数|完成率|出勤率|趋势|每周平均)'
)


def count_request_text(message):
    """Remove only entire explicit metric exclusions, never mixed/double negation."""
    # These clauses describe how to explain a zero result, not which states
    # to query. Whole-clause matching keeps genuine negative filters intact.
    explanation = re.compile(r'(?:即便|即使)(?:完成数|已完成数)为(?:零|0)|(?:也)?只说明(?:该状态的数量|这个口径)|(?:也)?(?:不要|别)(?:把完成数为(?:零|0))?说(?:成)?没有(?:任何)?训练记录|完成数为(?:零|0)也不代表没有记录|不推断我没有训练')
    clauses=[]
    for raw in re.split(r'[，,。；;！？!?\n]', message):
        clause=raw.strip()
        if not clause or _EXCLUDED_METRIC.fullmatch(clause) or explanation.fullmatch(clause):
            continue
        # Negating a false equivalence preserves completed as the positive
        # counting category; it does not request every other possible state.
        clause=re.sub(r'(?:也)?(?:不要|别)把((?:提前结束|进行中|跳过|放弃)(?:\s*(?:和|与|及|、)\s*(?:提前结束|进行中|跳过|放弃))*)(?:当成|当作|当|算成|算作|计入)已完成',r'排除\1',clause)
        clauses.append(clause)
    return '；'.join(clauses)


def count_evidence_query(message):
    """Separate a finite intensity subgoal without removing it from the task.

    This view is used only to supply count tool arguments. The original query,
    other evidence obligations, and final response still own the intensity goal.
    Unknown/conditional comparisons, extra time scopes and writes stay visible.
    """
    text = count_request_text(message)
    intensity = re.compile(r'(?:也请|也|并|请)?(?:判断(?:训练)?强度是否合适|评估(?:训练)?强度)')
    clauses = text.split('；')
    if not any(intensity.fullmatch(s) for s in clauses):
        return text
    qualifier = re.compile(r'如果记录不足以判断|记录不足以判断时说清缺失项|(?:请)?说清缺什么|已有的计数先交付')
    return '；'.join(s for s in clauses if not intensity.fullmatch(s) and not qualifier.fullmatch(s))


def _positive_clauses(message):
    return [s.strip() for s in re.split(r'[，,。；;！？!?\n]', message)
            if s.strip() and not _NEGATIVE_REQUEST.search(s)]


def task_read_evidence(message, *, domain, kind, effect, risk, evidence):
    """Return authorized evidence, optional hints, and exact task anchors."""
    original = list(dict.fromkeys(evidence))
    if domain == 'nutrition':
        from app.services.agent_nutrition_scope import nutrition_read_evidence
        return nutrition_read_evidence(message, domain=domain, kind=kind, effect=effect, risk=risk, evidence=original)
    if domain not in _WORKOUT | {'health'} or kind not in {'query', 'assessment'} or effect != 'read' or risk == 'high':
        return original, [], []
    clauses = _positive_clauses(message)
    positive = '；'.join(clauses)
    # A new symptom can make health the primary domain without removing the
    # user's explicit request for a temporary training plan. Anchor that
    # obligation in the original training clause, not a model domain label.
    temporary = any(_TEMPORARY.search(s) and (domain != 'health' or '训练' in s
                    or _SAVED_PLAN_REQUEST.search(s)) for s in clauses)
    counters = domain in _WORKOUT and bool(_PLAN_DAYS.search(positive) and _HISTORY_COUNT.search(positive))
    if not temporary and not counters:
        return original, [], []
    required = []
    anchors = []
    if temporary:
        required.extend(['active_plan', 'health_screening'])
        anchors.extend(s for s in clauses if _TEMPORARY.search(s))
    if counters:
        # Progress remains an obligation until a complete, source-verified
        # history result proves the same count. A truncated list is not enough.
        required.extend(['active_plan', 'workout_history', 'workout_progress'])
        anchors.extend(s for s in clauses if _PLAN_DAYS.search(s) or _HISTORY_COUNT.search(s))
    explicit = {
        'profile_summary': r'个人资料|我的资料|体重|身高|年龄|体脂|营养目标',
        'workout_progress': r'训练进度|容量|趋势|每周平均|最好成绩|评估[^；]{0,12}强度|判断[^；]{0,12}强度|强度是否',
        'workout_history': r'训练记录|实际记录|过去[^；]{0,8}练|最近练',
        'workout_daily_context': r'(?:今天|当天|今日)[^；]{0,10}(?:该练|练什么|安排|已练|练了什么)',
        'next_workout': r'下一练|下次(?:该|应该)?练|下一次训练安排',
        'active_workout_session': r'正在练|正在进行的训练|当前训练场次',
        'health_screening': r'伤病|疼痛|不适|身体限制|健康筛查',
    }
    for key, pattern in explicit.items():
        matches = [s for s in clauses if re.search(pattern, s)]
        if matches:
            required.append(key)
            anchors.extend(matches)
    # Nutrition/weight/food and other unrelated goals cannot silently disappear.
    required.extend(x for x in original if x not in _MANAGED)
    required = list(dict.fromkeys(required))
    optional = [x for x in original if x not in required]
    return list(dict.fromkeys(required + optional)), optional, list(dict.fromkeys(anchors))[:8]
