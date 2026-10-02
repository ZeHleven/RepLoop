"""Source-bound missing plan goals gate proposal creation, never grant writes."""
from __future__ import annotations

import re
from app.schemas.agent_task import PlanInputGap

_LABELS = {'schedule.duration_weeks':'计划周期（周）', 'schedule.days_per_week':'每周训练天数',
           'exercise.sets':'组数', 'exercise.reps':'次数', 'exercise.rest_seconds':'休息秒数',
           'exercise.recommended_weight_kg':'重量'}
_PENDING = re.compile(r'待(?:我)?补|待定|未定|没(?:有)?定|还没(?:确定|决定|定|给|提供|填|补|告诉)|(?:稍后|下一句|之后|以后)(?:我)?(?:再)?(?:补|给|提供|决定|定|告诉)|接着补|具体几|再告诉')
_WAIT = re.compile(r'(?:信息|要求|参数).{0,8}(?:补齐|齐全).{0,5}(?:再|后).{0,5}(?:出|生成)|先(?:别|不要)(?:出|生成).{0,5}提案')
_RELEASE = re.compile(r'信息(?:已|都)?齐|补充完毕|就这些|没有其他要求|现在(?:可以)?(?:出|生成).{0,5}提案')
_FIELDS = [(r'周期|几周','schedule.duration_weeks'), (r'每周.{0,4}天','schedule.days_per_week'),
           (r'组数|几组','exercise.sets'), (r'次数|几次','exercise.reps'),
           (r'休息|几秒','exercise.rest_seconds'), (r'重量|几公斤|几千克','exercise.recommended_weight_kg')]


def workflow_wait(quote: str) -> bool:
    """Only a pure generation precondition, not a value or apply permission."""
    return bool(_WAIT.search(quote) and not re.search(r'\d|组数|次数|重量|休息|周期',quote))


def _released(message):
    for clause in re.split(r'[，,。；;\n]', message):
        if re.fullmatch(r'\s*(?:请|现在|那就|可以|立即|帮我)*(?:生成|出)(?:完整|修改|调整|待确认)?提案[！!\s]*', clause):
            return True
        match = _RELEASE.search(clause)
        if match and not re.search(r'不要|不用|不能|别|不是|等|如果|假如|待|稍后', clause[:match.start()]):
            return True
    return False


def workflow_release(previous_quote: str, current_quote: str) -> bool:
    return workflow_wait(previous_quote) and _released(current_quote)


def _provided_value(change, message, *, targets=()):
    from app.services.agent_plan_quantities import provided_plan_value
    return provided_plan_value(change, message, targets=targets)


def fulfills_pending_requirement(previous_quote, current_quote, gaps, changes, message):
    """Only removal of a sourced missing-input requirement can be fulfillment."""
    deferred_condition = re.search(r'等.{0,12}(?:补充|补齐).{0,12}(?:再|后).{0,8}(?:出|给|生成).{0,6}提案', previous_quote)
    if not current_quote.strip() or current_quote not in message or not (_PENDING.search(previous_quote) or deferred_condition):
        return False
    linked = [g for g in gaps if g.field_path is not None
              and (previous_quote in g.quote or g.quote in previous_quote)]
    if not linked:
        return False
    targets = {c.target_reference for c in changes if c.target_reference}
    deferred = {_key(g) for g in _explicit_gaps(message, changes)}
    return all(_key(g) not in deferred and any(
        c.resource == 'workout_plan' and c.operation == 'update'
        and c.field_path == g.field_path and c.target_reference == g.target_reference
        and (_provided_value(c, current_quote, targets=targets)
             or _released(current_quote) and _released(message))
        and _provided_value(c, message, targets=targets)
        for c in changes) for g in linked)


def _key(gap):
    return (gap.field_path, gap.target_reference, gap.quote if gap.field_path is None else '')


def _explicit_gaps(message, changes):
    """Finite fallback for explicit deferred input; not universal extraction."""
    gaps=[]
    for sentence in re.split(r'[。；;\n]',message):
        if not sentence.strip() or not _PENDING.search(sentence):
            continue
        fields=[field for pattern,field in _FIELDS if re.search(pattern,sentence)]
        for field in fields:
            targets={c.target_reference for c in changes if c.resource=='workout_plan' and c.target_reference and c.target_reference in sentence}
            target=next(iter(targets)) if len(targets)==1 and field.startswith('exercise.') else None
            gaps.append(PlanInputGap(field_path=field,target_reference=target,quote=sentence.strip()[:400]))
        if not fields and _WAIT.search(message):
            gaps.append(PlanInputGap(field_path=None,quote=sentence.strip()[:400]))
    if not gaps and _WAIT.search(message) and not _RELEASE.search(message):
        gaps.append(PlanInputGap(field_path=None,quote=message[:400]))
    return gaps


def enforce_plan_completeness(resolution, state, message):
    """Retain prior gaps until a corresponding explicit update or withdrawal."""
    if resolution.intent_domain!='workout_plan' or resolution.request_kind!='mutation' or resolution.requested_effect!='update':
        return resolution
    task=next((t for t in state.tasks if t.id==state.active_task_id and t.phase!='cancelled'),None)
    prior=list(task.plan_input_gaps) if task and state.transition in {'continue','resume'} else []
    # An expired/applied proposal must not carry goals into another draft.
    if task and task.proposal_id and not task.proposal_pending:
        prior=[]
    incoming=list(resolution.plan_input_gaps)
    for gap in incoming:
        if gap.quote not in message and not any(_key(gap)==_key(old) and gap.quote==old.quote for old in prior):
            raise ValueError('task_plan_gap_not_user_quote')
    discovered=_explicit_gaps(message,resolution.change_requests)
    current={_key(g):g for g in [*prior,*incoming,*discovered]}
    newly_deferred={_key(g) for g in discovered}
    remaining=[]
    for key,gap in current.items():
        matching=[c for c in resolution.change_requests if c.resource=='workout_plan' and c.operation=='update'
                  and c.field_path==gap.field_path and c.target_reference==gap.target_reference and c.value is not None]
        withdrawn=any(w.field_path==gap.field_path and w.target_reference==gap.target_reference and w.quote in message for w in resolution.pending_plan_withdrawals)
        # Never use an existing plan default, inherited goal, or a guessed value
        # to satisfy a currently deferred field. The current message must name
        # a value, or an explicit sourced withdrawal of this exact field.
        targets={c.target_reference for c in resolution.change_requests if c.target_reference}
        explicit_value=any(_provided_value(c,message,targets=targets) for c in matching)
        released=gap.field_path is None and _released(message) and not _WAIT.search(message)
        if key not in newly_deferred and (explicit_value or withdrawn or released):
            continue
        remaining.append(gap)
    if len(remaining)>12:
        raise ValueError('task_plan_gap_capacity_exceeded')
    if task:
        task.plan_input_gaps=remaining
    values={'plan_input_gaps':remaining}
    if remaining:
        slots=list(dict.fromkeys((g.target_reference or '')+_LABELS.get(g.field_path,'待补的计划要求') for g in remaining))[:8]
        values.update(clarification_required=True,missing_slots=slots,
                      clarification_question='已保留明确的调整目标。请补充'+ '、'.join(slots)+'，信息齐全后再生成完整提案。')
    return resolution.model_copy(update=values)
