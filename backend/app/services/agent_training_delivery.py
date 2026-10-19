"""Small, source-bound training deliveries with checked arithmetic.

This is not a free-text judge. It owns only fully recognized read requests;
unknown clauses, missing evidence and unsupported prescriptions keep the normal
agent path. Observations must match the executed action and durable fingerprint.
"""
from dataclasses import dataclass
from datetime import timedelta
import json
import math
import re
from langchain_core.messages import ToolMessage
from app.services.business_clock import business_today
from app.services.agent_trace import observation_fingerprint
from app.services.agent_tools import TOOL_ID_BY_LANGCHAIN_NAME
from app.services.agent_task_evidence import count_request_text, count_evidence_query
from app.services.agent_count_evidence import _count_scope, history_count_observation
from app.services.history_status_scope import STATUS_LABELS
from app.schemas.agent_trace import AgentTrainingDeliveryTrace


@dataclass(frozen=True)
class DeliveryRequest:
    mode: str
    weeks: int = 1
    minutes: int | None = None
    weight: bool = False
    intensity: bool = False
    symptom_parts: tuple = ()


# A full token consumption check prevents a known prefix from swallowing an
# unrelated second goal. New vocabulary alone never authorizes a write.
_COMMON = '''请 分别 逐项 先 再 也 另外 同时 以及 和 与 及 并 而 且 或 的 地 得 这 那 个 项 份 两项 两个 两者 两种 对应 其 数 列
我 你 给我 给 告诉我 报出 提供 只给 只要 只提供 只 建议 文字建议 文字 不保存 不要保存 不修改 不修改计划 先不修改计划
这份 该 本次 一起 同一项 只说明 准确 明确 具体 核对 核查 看一下 看看 查询 统计 比较 说明 解释 给出
不混在一起 不要把两项混在一起 不要混为同一项 不要把计划0日当作没有训练记录
依据 根据 基于 按 结合 参考 已保存 已有 保存的 当前 现有 生效 原 计划 训练计划 实际 实际记录 历史
本周 这周 截至 到 今天 日期 日期范围 统计日期 范围 口径 含义 差别 区别 数量 各是多少 多少个 几次 几天
已完成 完成 训练 训练记录 训练次数 训练场次 场次 计划训练日 训练日 完成日 完成训练日 完成数
日数 结果 只报 两项结果 状态 已完成训练次数 查 两个数各代表什么
当前计划显示完成训练日为0 这是否就表示没有训练记录
不推断强度 不用据此判断强度 不要判断强度 不判断强度
判断训练强度是否合适 判断强度是否合适 评估强度 如果记录不足以判断 记录不足以判断时 说清缺什么 说清缺失项 已有的计数先交付
最近 工作 比较 很 忙又累 忙累 累 加班 加班后 疲劳 疲惫 不想练得太久
临时 临时安排 临时训练建议 临时训练安排 过渡 过渡建议 过渡方案 过渡安排 过渡训练建议 过渡训练安排
减量 减量建议 减量方案 保底 保底安排 调整规划 安排 方案 规划
一周 两周 二周 1周 2周 接下来 训练建议 每次 单次 最多 不超过 上限 以内
身体限制 健康筛查 已有健康资料 健康资料 资料 档案 档案里 记录 已经在 伤病记录
膝盖 膝部 踝关节 不适 疼痛 不适记录
状态更差时 状态差时 状态更差 状态差 状态不好 低状态 累得厉害时
允许休息 可以休息 可休息 休息 怎么办 如何 怎样 怎么 写清 写明 需要 时
恢复后 恢复 好转后 逐步 回归 回到 回来 回去 加回来 加回 原计划 生效计划 条件 步骤
轻重怎么选 动作与量 用力程度 哪些情况 体重 已保存体重 一周过渡建议
不想在健身上投入太大的强度和时间 但又担心练不到位 不想在健身上投入太大的强度和时间但又担心练不到位
做 说清 时间不足时 做不完时 想用 取舍
'''.split()
_TOKENS = re.compile('|'.join(re.escape(t) for t in sorted(set(_COMMON), key=len, reverse=True)))


def delivery_request(query):
    if not isinstance(query, str) or len(query) > 800:
        return None
    clean = count_request_text(query)
    # Status operations already have a strict finite parser; consume them only
    # after that parser has accepted their meaning in a counter request.
    temporary = bool(re.search(r'临时|过渡|减量|保底', clean) or
                     re.search(r'调整规划', clean) and re.search(r'工作.*累|疲劳|疲惫', clean))
    if temporary:
        ws = {int(n) if n.isdigit() else {'一':1,'二':2,'两':2}[n]
              for n in re.findall(r'([12一二两])周', clean)}
        ms = {int(n) for n in re.findall(r'(\d+)分钟', clean)}
        if len(ws) > 1 or len(ms) > 1 or ms and not 5 <= next(iter(ms)) <= 60:
            return None
        residual = re.sub(r'\d+分钟', '', clean)
        mode = 'temporary_training'
    else:
        if not re.search(r'计划.*(?:完成|训练日)', clean) or not re.search(r'场次|训练次数|几次训练|实际记录|训练记录', clean):
            return None
        from app.services.history_status_scope import parse_history_status_scope
        if not parse_history_status_scope(clean).complete:
            return None
        residual = count_evidence_query(clean)
        # A complete exclusion clause is already interpreted by the finite
        # status parser. Do not strip arbitrary state words from other goals.
        operand = r'(?:提前结束|进行中|正在进行|跳过|放弃)(?:\s*(?:和|与|及|、)\s*(?:提前结束|进行中|正在进行|跳过|放弃))*'
        residual = '；'.join(s for s in residual.split('；') if not re.fullmatch(
            rf'(?:排除{operand}|(?:不要|不)把{operand}(?:的)?(?:训练)?(?:场次|记录)?计入|{operand}(?:的)?(?:训练)?(?:场次|记录)?(?:都|也)?(?:不要算作|不计入|不算作)已完成)', s))
        mode = 'training_counters';ws = set();ms = set()
    residual = _TOKENS.sub('', residual)
    if residual.strip(' ，,。；;！？!?、：:（）()\n\t'):
        return None
    return DeliveryRequest(mode, next(iter(ws), 1), next(iter(ms), None),
                           '体重' in clean,
                           bool(re.search(r'评估强度|判断(?:训练)?强度是否合适', clean)),
                           tuple(p for p in ('膝', '踝') if p in clean))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _label(value):
    # Business strings are displayed as labels, never interpreted as commands
    # or as Markdown links supplied by another data source.
    return re.sub(r'[\[\]<>`*_\\]', '', re.sub(r'\s+', ' ', str(value)))[:100]


def _observations(trace, result):
    records = {}
    for message in result.get('messages', []):
        if not isinstance(message, ToolMessage) or message.status != 'success':
            continue
        name = TOOL_ID_BY_LANGCHAIN_NAME.get(message.name)
        actions = [a for a in trace.actions if a.call_id == message.tool_call_id and a.tool_id == name and a.status == 'completed']
        observed = [o for o in trace.observations if o.call_id == message.tool_call_id and o.tool_id == name and o.status == 'success']
        fingerprint = observation_fingerprint(message.content)
        if len(actions) != 1 or len(observed) != 1 or observed[0].result_fingerprint != fingerprint:
            continue
        try:
            data = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            records[name] = (data, actions[0].arguments, message.tool_call_id, fingerprint)
    return records


def _receipt(mode, records, used, **values):
    return AgentTrainingDeliveryTrace(mode=mode, source_call_ids=[records[k][2] for k in used],
        source_fingerprints=[records[k][3] for k in used], **values)


def _counter_delivery(query, request, records):
    today = business_today()
    scope = _count_scope(query, today)
    plan_result = records.get('plan.get_active')
    if not scope or not plan_result:
        return None
    start, end, _, statuses = scope
    week_start = today - timedelta(days=today.weekday())
    if start != week_start or end != today:
        return None  # Active-plan counter is scoped only to this week to date.
    data = plan_result[0];plan = data.get('plan');ps = data.get('scope', {})
    if (data.get('found') is not True or not isinstance(plan, dict)
            or ps.get('as_of') != today.isoformat() or ps.get('week_start') != start.isoformat()
            or ps.get('timezone') != 'Asia/Shanghai'
            or ps.get('completed_days_basis') != 'current_plan_continuity_and_scheduled_weekday'
            or type(plan.get('weekly_completed_days')) is not int or not 0 <= plan['weekly_completed_days'] <= 7):
        return None
    count = None;partial = False;used = ['plan.get_active']
    history = records.get('workout.list_history')
    if request.intensity:
        # This fixed explanation is appropriate only for a returned history
        # with no actual exercise/effort detail. With real detail, the semantic
        # path must assess it instead of pretending the user never supplied it.
        sessions=history[0].get('sessions') if history else None
        if (not isinstance(sessions,list) or any(not isinstance(s,dict)
                or s.get('total_sets')!=0 or s.get('exercises')!=[]
                or s.get('feedback') not in (None,{}) for s in sessions)):
            return None
    if history:
        checked = history_count_observation(history[0], history[1], scope, today)
        if checked:
            partial = not checked[1]
            if checked[1]:count = checked[0];used.append('workout.list_history')
    aggregate = records.get('workout.get_progress')
    if count is None and aggregate:
        agg, args, _, _ = aggregate;actual = agg.get('count_scope', {})
        if (agg.get('aggregation') == 'all_matching_sessions'
                and type(agg.get('total_sessions')) is int and agg['total_sessions'] >= 0
                and actual.get('start_date') == args.get('start_date') == start.isoformat()
                and actual.get('end_date') == end.isoformat()
                and actual.get('as_of') == today.isoformat() and actual.get('timezone') == 'Asia/Shanghai'
                and set(actual.get('statuses', [])) == set(args.get('statuses', [])) == set(statuses)):
            count = agg['total_sessions'];used.append('workout.get_progress')
    if count is None:
        return None
    labels = '、'.join(STATUS_LABELS[s] for s in statuses)
    plan_days = plan['weekly_completed_days']
    count_label = f'历史已完成{count}场' if statuses == ('completed',) else f'历史训练场次（{labels}）：{count}场'
    lines = [f'核对范围：{start.isoformat()} 至 {end.isoformat()}，含首尾；截至今天，Asia/Shanghai。',
        count_label+'。这是该日期范围内符合状态筛选的记录条数，同一天可有多场；不要求关联当前计划。',
        f'计划关联完成{plan_days}日。按当前生效计划、与计划安排连续关联的本周已完成训练日统计；这不是每周安排几天或截至今天应练几天。',
        '两项是不同口径，不能直接相减。计划完成日为0不代表没有训练记录；某状态计数为0也不代表其他状态没有记录。',
        '上周及今天之后的场次均未纳入这个范围；未纳入不等于没有记录。']
    if partial:
        lines.append('历史明细列表被截断，以上场次总数来自完整范围聚合；不能用这部分明细判断全部日期分布或某天没有训练。')
    if request.intensity:
        lines.append('强度是否合适：这些计数不足以作判断。还需实际动作、每组次数与负重、余力或RPE，以及练后疲劳和恢复反馈；计划处方不能当作已完成的数据。')
    else:
        lines.append('这些是记录计数，不据此推断实际训练强度或训练效果。')
    return '\n'.join(lines), _receipt('training_counters', records, used,
        history_session_count=count, plan_completed_days=plan_days)


def _temporary_delivery(request, records):
    if not {'plan.get_active', 'health.get_screening_summary'} <= records.keys():
        return None
    pd = records['plan.get_active'][0];health = records['health.get_screening_summary'][0]
    plan = pd.get('plan')
    if (pd.get('found') is not True or not isinstance(plan, dict) or plan.get('is_active') is not True
            or health.get('found') is not True or health.get('screening_completed') is not True
            or health.get('chronic_conditions') or not isinstance(health.get('injuries'), list)
            or any(not isinstance(i, str) for i in health['injuries'])
            or any(not any(p in i for i in health['injuries']) for p in request.symptom_parts)
            or plan.get('safety_status') not in (None, 'compatible')):
        return None
    from app.services.agent_training_constraints import training_schedule, adjustment_budget
    schedule = training_schedule(pd)
    if schedule is None:return None
    exercises = plan['exercises']
    if any(not ex.get('exercise_name') or ex.get('safety_notice') for ex in exercises):
        return None
    spec = adjustment_budget(schedule, request.minutes)
    if spec is None:return None
    days = spec['baseline_days'];baseline = spec['baseline_weekly_sets']
    temporary_days = spec['temporary_days'];adjusted = spec['temporary_weekly_sets'];sessions = []
    for session in spec['sessions']:
        chosen = []
        for item in session['exercises']:
            ex = exercises[item['source_index']]
            chosen.append({'index':item['source_index'], 'name':_label(ex['exercise_name']),
                           'sets':item['sets'], 'reps':item['reps'], 'rest':item['rest_seconds'],
                           'original_sets':ex['sets'], 'original_reps':ex['reps']})
        sessions.append(chosen)
    used = ['plan.get_active', 'health.get_screening_summary'];lines = []
    if request.weight:
        profile = records.get('profile.get_summary');weight = profile[0].get('weight_kg') if profile and profile[0].get('found') is True else None
        if _number(weight) and weight > 0:
            used.append('profile.get_summary');lines.append(f'已保存档案体重：{weight:g} kg。该值来自个人资料，不据此推断体重趋势。')
        else:
            logs = records.get('weight.list_history');scope = logs[0].get('scope', {}) if logs else {}
            rows = logs[0].get('records') if logs else None
            if (not isinstance(rows, list) or not rows or scope.get('as_of') != business_today().isoformat()
                    or scope.get('selection') != 'latest_records' or not isinstance(rows[0], dict)
                    or not _number(rows[0].get('weight_kg')) or rows[0]['weight_kg'] <= 0):return None
            weight = rows[0]['weight_kg'];used.append('weight.list_history')
            lines.append(f'已保存体重记录中的最近一条：{weight:g} kg。这里只核对保存值，不据此推断体重趋势。')
    injuries = '、'.join(_label(x) for x in health['injuries'])
    lines += [f'当前生效计划“{_label(plan.get("name", "当前计划"))}”：每周{days}天，计划动作合计每周{baseline}组。这是处方安排，不是实际完成量或个人强度证明。',
        '健康筛查记录：'+(injuries if injuries else '未记录伤病')+'。筛查记录不等于已经确认所有动作适合今天的身体状态。']
    profile = records.get('profile.get_summary')
    if profile and type(profile[0].get('training_days_per_week')) is int and profile[0]['training_days_per_week'] != days:
        if 'profile.get_summary' not in used:used.append('profile.get_summary')
        lines.append(f'档案偏好每周{profile[0]["training_days_per_week"]}天，与生效计划不同；下面按生效计划的{days}天作为调整基准。')
    today = business_today();duration = request.weeks*7
    lines.append(f'临时建议：从{today.isoformat()}起至{(today+timedelta(days=duration-1)).isoformat()}，共{duration}天。')
    if request.minutes is None:
        estimates = '、'.join(f'第{i+1}次约{math.ceil(s["estimated_seconds"]/60)}分钟' for i,s in enumerate(spec['sessions']))
        lines.append('你没有给出每次的硬时限；'+(estimates+'（含热身、每组后休息和收尾）' if estimates else '这一版暂不安排原力量工作组')+'。这些是建议用时的估算，不是你设定的时间上限。')
    else:
        lines.append(f'每次最多{request.minutes}分钟，包含热身、动作、切换、休息和收尾。')
    if not adjusted:
        lines += [f'在这份减量安排中，每7天原力量工作组从{baseline}组减至0组，不安排原力量工作组，也不额外加天或补课。',
                  '明确取舍：这次无法完整覆盖原计划的力量训练目标；可休息，或只选不诱发不适的轻活动，不再塞入一组动作。']
        if baseline == 1:
            lines.append('原计划每周只有1组；严格减少周组数后只能为0。要恢复力量工作组，需要先确认调整减量目标，再重新核对时限和身体状态，不能一边称为减组一边追加工作组。')
        elif request.minutes is not None:
            lines.append('当前时限不容纳本方案的工作组；精神好一些也不改变这个边界。要恢复工作组，需先由你确认放宽时限，再重新计算，不能靠缩短休息凑进去。')
    else:
        lines.append(f'每7天最多{temporary_days}次，周工作组合计从{baseline}组减至最多{adjusted}组；不额外加天或补课。'+('两次之间至少隔一天，挑状态较好的日子。' if temporary_days>1 else '挑状态较好的那天。'))
        lines.append('明确取舍：只保留以下动作和组数，省略其余原安排，不声称这份减量版完整覆盖原训练目标。')
    for si, session in enumerate(sessions):
        lines.append(f'第{si+1}次：先用约3分钟轻活动并熟悉当天动作，再做以下内容。')
        for ex in session:
            lines.append(f'- {ex["name"]}：{ex["sets"]}组×{ex["reps"]}次，组间休息{ex["rest"]}秒（该计划日原安排{ex["original_sets"]}组×{ex["original_reps"]}次）。')
    lines += [('留约1分钟收尾。以上时间分配是估算；热身、动作切换和休息都计时，'+('到上限即结束，' if request.minutes is not None else '实际用时过长时减组或结束，')+'未做的组直接省略，不压缩必要休息或赶动作。') if adjusted else '这次只休息或轻活动，不把减量方案中的0组改成临时追加的力量组。',
        ('恢复到允许安排工作组后，' if not adjusted else '')+'负重按当天能保持动作稳定、每组结束仍有约3–4次余力来选。不要直接照搬计划公斤数；不确定就从轻负重试起，明显吃力便减重或停止，不追求力竭。',
        '状态更差时可以只轻活动，或直接休息；跳过的课不补练。已有不适部位在动作中疼痛、疼痛加重或明显不稳时，停掉该动作，不把浅蹲、静蹲等替代动作当作必然无负荷。持续或加重的不适需专业评估。',
        '出现胸痛、呼吸困难、晕厥或急性剧痛时停止训练并及时就医。',
        f'回归条件：先等日常精力、睡眠和不适改善；用当前减量版练后及次日没有异常疲劳或不适加重，再逐次恢复。每次只给一个动作增加1组，最多回到该计划日原组数；保持每7天不超过原计划{days}天，不先加频率。',
        '组数稳定后再恢复原次数，最后才按仍有余力、动作稳定且次日恢复的条件小幅调整负重。计划公斤数不是必须达到的安全目标，不规定第几次训练必须回到某个重量。若状态退步，退回前一步或休息。',
        '这是一份暂时的省时省力建议，不能保证训练效果不变；期限结束仍未恢复就继续评估，不自动加量。没有保存或修改你的计划。']
    if temporary_days < days:
        lines.insert(-1, f'等单次训练量稳定恢复、训练后及次日恢复良好，再逐次加回省略的原计划训练日，最多恢复到每7天{days}次；周组数不超过原计划{baseline}组。每增加一天就先观察恢复，状态变差立即退回。')
    return '\n'.join(lines), _receipt('temporary_training', records, used, version=2,
        baseline_weekly_sets=baseline, temporary_weekly_sets=adjusted, temporary_days=temporary_days,
        duration_days=duration, session_cap_minutes=request.minutes,
        session_cap_source='user' if request.minutes is not None else 'unspecified',
        estimated_session_seconds=[s['estimated_seconds'] for s in spec['sessions']],
        allocation_status=spec['allocation_status'])


def ground_training_delivery(reply, trace, *, result, query, resolution):
    if (result.get('read_error_code') or trace.terminal_action != 'answer'
            or trace.execution_mode != 'direct' or trace.risk_level == 'high'
            or not trace.request_context or trace.request_context.as_of != business_today().isoformat()
            or not trace.evidence_contract or trace.evidence_contract.status != 'complete'
            or resolution.request_kind not in {'query','assessment'} or resolution.requested_effect != 'read'
            or resolution.change_requests or resolution.clarification_required):
        return reply, trace
    request = delivery_request(query)
    if not request:return reply, trace
    records = _observations(trace, result)
    rendered = (_temporary_delivery(request, records) if request.mode == 'temporary_training'
                else _counter_delivery(query, request, records))
    if not rendered:return reply, trace
    text, receipt = rendered
    return text, trace.model_copy(update={'training_delivery':receipt})
