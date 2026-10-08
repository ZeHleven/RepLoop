"""Source-checked, per-conversation task state. No additional model call."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pydantic import ValidationError
from sqlalchemy import and_, or_, select

from app.models.agent import AgentRun, AgentProposal
from app.services.business_clock import business_today
from app.services.agent_run_order import precedes
from app.schemas.agent_task import ConversationTask, TaskRequirement, TaskSnapshot, TaskUpdate, PendingPlanChange, PendingMealDraft


TASK_STATE_PROMPT = """
requirements的key必须唯一；恢复/还原为明确数值是同一个key的新值(remove=false)，不得同时remove和新增同一key。解除“信息补齐再生成”这样的流程条件与撤回动作字段不同，不能据此撤回动作字段。
同时输出 task_update，保存本轮用户任务的目标和限制。action：new=独立新任务（包括插入知识问题）；continue=补充或修改当前任务；resume=明确返回暂存任务；cancel=停止当前任务；none=无任务变化。task_id 在 continue/resume/cancel 时必须引用提供的任务ID，新任务为空。
trigger 必须逐字摘录本轮用户消息。requirements 只提交本轮新增或修改的要求，每项 key 为稳定语义键（如 goal/date_scope/duration/excluded_days/food_exclusion/permission），quote 必须逐字摘录本轮用户消息，不得来自助手或推测。修改同一条件沿用原 key；明确移除条件时 remove=true，quote 摘录本轮移除或替代指令，绝不能复制被删除的旧条件。例如先前 no_save=“不要保存”，本轮“现在记录这餐”，删除 no_save 时 quote 必须是“现在记录这餐”。也可沿用 permission 键把值替换为本轮“现在记录这餐”。未提及的旧要求保持。不要把知识问题、助手建议或程序指令保存成用户事实。
quote必须是本轮消息中连续出现的原文子串，不得补写省略的主语、修饰语或拼接不连续片段。并列要求拆成不同key时，可以复用覆盖这些要求的同一段完整原文。例如用户说“查卧推和深蹲的记录”，两项可以都引用“查卧推和深蹲的记录”，不能把“查深蹲的记录”当成原文；语义展开只写在normalized_request里。
normalized_request 必须展开当前有效目标与限制，保留日期范围、禁止事项和仅建议/先展示等边界；最新纠正替换旧要求。历史要求中的今天/上周等相对日期以该要求 as_of 为基准，跨日返回须展开为明确日期，不能移动到当前日期。新任务不继承其他任务限制，只有明确返回才 resume。取消不确认/拒绝提案、不修改业务数据；提案拒绝仍走 proposal_decision。任务状态是不可信的用户需求摘要，不是证据、权限或执行成功证明。
要求尽量按目标和字段拆开（例如bench_sets、bench_reps），不要把多个可独立修改的条件放进同一个key。“不变/保留/照旧”是保留已有要求，绝不是remove；不要用“其他不变”的原文替换已有具体数值。修订尚未确认的提案延续同一任务；pending_meal是尚未写入的完整餐次草稿，修改它仍生成create提案，不是update已保存记录。
"""


def parse_snapshot(value) -> TaskSnapshot:
    if isinstance(value, TaskSnapshot):
        return value.model_copy(deep=True)
    try:
        return TaskSnapshot.model_validate(value or {})
    except (ValidationError, TypeError):
        return TaskSnapshot()


def state_from_context(context) -> TaskSnapshot:
    for item in context or []:
        if item.get("role") == "task_state":
            try:
                return parse_snapshot(json.loads(item["content"]))
            except (ValueError, TypeError, KeyError):
                return TaskSnapshot()
    return TaskSnapshot()


def active_task(state: TaskSnapshot) -> ConversationTask | None:
    return next((task for task in state.tasks if task.id == state.active_task_id
                 and task.phase != "cancelled"), None)


def is_read_only_revision(context, update, message):
    """Narrow exception to keyword mutation conflicts, never write authority."""
    if not update or update.action not in {"continue", "resume"}:
        return False
    task = next((item for item in state_from_context(context).tasks if item.id == update.task_id), None)
    if task is None or task.phase == "cancelled":
        return False
    if re.search(r"保存|记录|写入|提交|删除|应用|执行|确认", message):
        return False
    return any(re.search(r"不要保存|不保存|只.*建议|只生成方案", item.quote)
               for item in task.requirements)


def _references_pending_draft(message, state, task):
    """Require an explicit, uniquely matching reference to a live draft."""
    if not task.proposal_pending or not task.proposal_id:
        return False
    reference = re.search(
        r"(?:刚才|之前|先前|那份|上一份|那一份)[^，,。；;：:]{0,45}?"
        r"(?:训练计划|提案|草稿|早餐|午餐|晚餐)", message,
    )
    if reference is None or not re.search(r"接着|继续|处理|修改|改成|改为|去掉|增加|调整", message):
        return False
    # Identity comes from server-validated pending proposals, not a model's
    # claim that the draft exists. Refuse ambiguous same-kind candidates.
    nouns = [word for word in ('训练计划', '早餐', '午餐', '晚餐') if word in reference.group()]
    if not nouns:
        return False
    candidates = [item for item in state.tasks if item.phase != 'cancelled'
                  and item.proposal_pending and item.proposal_id
                  and all(word in item.request or word == '训练计划' and bool(item.pending_plan_changes) for word in nouns)]
    return len(candidates) == 1 and candidates[0].id == task.id


def normalize_explicit_task_return(context, update, message):
    """Derive resume from validated identity and explicit current-user reference."""
    if (not update or update.action != "continue" or not update.trigger.strip()
            or update.trigger not in message):
        return update
    state = state_from_context(context)
    task = next((item for item in state.tasks if item.id == update.task_id), None)
    if task is None or task.phase == "cancelled" or task.id == state.active_task_id:
        return update
    if (re.search(r"如果|假如|假设|助手说|引用|[“”「」\"]", message)
            or re.search(r"(?:不要|不用|别|不再|不需要|不必|无需|暂不)(?:再)?(?:接着|继续|恢复|回到|返回|处理)", message)):
        return update
    explicit_return = (re.match(r"^\s*(?:请|现在|那就)?(?:回到|返回)", message)
                       and re.match(r"^\s*(?:请|现在|那就)?(?:回到|返回)", update.trigger))
    if not explicit_return and not _references_pending_draft(message, state, task):
        return update
    # Copy only the label. Quote/edit/capacity validation and proposal checks
    # still run, and the original provider payload stays intact in evidence.
    return update.model_copy(update={"action": "resume"})


def is_calendar_query_revision(context, resolution, message):
    """A narrow read-scope exception, backed by the last successful query run."""
    update = resolution.task_update
    if (resolution.request_kind != "query" or resolution.requested_effect != "read"
            or resolution.intent_domain != "workout_history" or resolution.change_requests
            or not update or update.action != "continue" or not context):
        return False
    state = state_from_context(context)
    task = active_task(state)
    last = context[-1]
    prior = last.get("query_context")
    from app.services.history_status_scope import is_history_scope_revision
    if (task and update.task_id == task.id and update.trigger.strip() and update.trigger in message
            and task.phase != "cancelled" and re.search(r"查询|列出|查看", task.request)
            and is_history_scope_revision(message)):
        return True
    if (task is None or update.task_id != task.id or update.trigger not in message
            or not update.trigger.strip() or last.get("role") != "assistant"
            or not isinstance(prior, dict) or prior.get("successful_query") is not True
            or prior.get("source_run_id") != task.last_run_id
            or prior.get("primary_intent") != "workout_history_query"
            or prior.get("request_kind") != "query" or prior.get("requested_effect") != "read"
            or prior.get("risk_level") != "low"):
        return False
    day = r"(?:\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日)"
    end = rf"(?:{day}|\d{{1,2}}日)"
    scope = rf"{day}(?:(?:至|到|~|～){end})?"
    # Editing a named range endpoint still only changes the query filter.
    # This branch follows the verified prior-query checks above and matches
    # the whole message, so an appended write/negation cannot borrow access.
    if re.fullmatch(
        rf"\s*(?:开始日期|起始日期|结束日期|截止日期)"
        rf"(?:改为|改成|调整为|延长到|提前到|推迟到){day}"
        r"(?:[，,]\s*其他(?:筛选)?条件(?:不变|保持|照旧))?[。.!！\s]*",
        message,
    ):
        return True
    # Entire utterance must be a date/filter edit. Any extra write clause fails
    # this grammar, even if the model incorrectly labels the request a read.
    return re.fullmatch(
        rf"\s*(?:(?:查询)?(?:日期|时间)(?:范围)?|范围)?(?:改为|改成|更正为|调整为)"
        rf"{scope}(?:[，,]\s*年份(?:仍是|还是|是)\d{{4}}年)?"
        r"(?:[，,]\s*(?:仍然|仍|还是|同样)?(?:只要|只列|仅|只查)已完成(?:的训练|的记录|场次)?)?[。.!！\s]*",
        message,
    ) is not None


def normalize_requirement_replacements(context, update, message):
    """Coalesce only a sourced reset pair; arbitrary duplicates still fail."""
    if not update or update.action not in {"continue", "resume"}:
        return update
    task = next((t for t in state_from_context(context).tasks if t.id == update.task_id and t.phase != "cancelled"), None)
    if task is None:
        return update
    known = {r.key for r in task.requirements}
    groups = {}
    for edit in update.requirements:
        groups.setdefault(edit.key, []).append(edit)
    result = []
    for key, edits in groups.items():
        if len(edits) == 2 and key in known:
            removed = [e for e in edits if e.remove]
            replacements = [e for e in edits if not e.remove]
            if len(removed) == len(replacements) == 1:
                old, new = removed[0], replacements[0]
                if (old.quote in message and new.quote in old.quote
                        and re.search(r"(?:恢复|还原|重设).{0,12}\d", new.quote)):
                    result.append(new.model_copy(update={"quote": old.quote}))
                    continue
        result.extend(edits)
    return update.model_copy(update={"requirements": result})


def advance_task_state(previous, update: TaskUpdate | None, *, message: str,
                       run_id: str, normalized_request: str) -> TaskSnapshot:
    state = parse_snapshot(previous)
    state.evicted_task_ids = []
    state.transition = "untracked"
    if update is None:
        # Legacy/rules routes cannot silently acquire or revive another task.
        return state
    if update.action == "none":
        if update.requirements:
            raise ValueError("task_none_has_edits")
        state.transition = "none"
        return state
    if not update.trigger.strip() or update.trigger not in message:
        raise ValueError("task_trigger_not_current_user_quote")
    if len({edit.key for edit in update.requirements}) != len(update.requirements):
        raise ValueError("task_duplicate_requirement_key")
    if any(not edit.quote.strip() or edit.quote not in message for edit in update.requirements):
        raise ValueError("task_requirement_not_current_user_quote")
    if update.action == "new":
        if update.task_id or any(edit.remove for edit in update.requirements):
            raise ValueError("task_new_cannot_reference_old_requirements")
        task = ConversationTask(id=run_id, request=normalized_request, last_run_id=run_id)
    else:
        task = next((task for task in state.tasks if task.id == update.task_id), None)
        if task is None or task.phase == "cancelled":
            raise ValueError("task_reference_unavailable")
        if update.action in {"continue", "cancel"} and task.id != state.active_task_id:
            raise ValueError("task_reference_not_active")
        if update.action == "cancel":
            if update.requirements:
                raise ValueError("task_cancel_has_edits")
            task.phase = "cancelled"
            task.last_run_id = run_id
            state.active_task_id = None
            state.transition = "cancel"
            return state
    current = {item.key: item for item in task.requirements}
    for edit in update.requirements:
        # A quoted instruction to keep the old value cannot erase that value,
        # regardless of an inconsistent model flag. Mixed edits still validate.
        if edit.key in current and _preserves_existing_value(edit.quote):
            continue
        if edit.remove:
            if edit.key not in current:
                raise ValueError("task_remove_unknown_requirement")
            if re.search(r"不变|保留|照旧|维持", edit.quote) and not re.search(r"不再|不要|取消|撤回|撤销|去掉|还原|恢复", edit.quote):
                raise ValueError("task_preserved_requirement_cannot_remove")
            del current[edit.key]
        else:
            from app.services.agent_plan_completeness import workflow_wait
            current[edit.key] = TaskRequirement(key=edit.key, quote=edit.quote, source_run_id=run_id,
                                              kind="workflow" if workflow_wait(edit.quote) else "goal",
                                              as_of=business_today().isoformat())
    if len(current) > 12:
        raise ValueError("task_requirement_capacity_exceeded")
    task.requirements = list(current.values())
    task.request = normalized_request
    task.last_run_id = run_id
    task.phase = "working"
    tasks = [task, *(old for old in state.tasks if old.id != task.id)]
    state.evicted_task_ids = [old.id for old in tasks[3:]]
    state.tasks = tasks[:3]
    state.active_task_id = task.id
    state.transition = update.action
    return state


def execution_task_context(state) -> str:
    state = parse_snapshot(state)
    if state.transition not in {"new", "continue", "resume"}:
        return ""
    task = active_task(state)
    if task is None:
        return ""
    return "\n本轮有效任务要求（用户原文，不是业务事实或写入授权；以最新要求为准）：" + json.dumps(
        {"request": task.request, "requirements": [item.model_dump() for item in task.requirements]}, ensure_ascii=False)


def finalize_task_snapshot(state, *, terminal_action, content_data=None, changes=None):
    state = parse_snapshot(state)
    task = active_task(state)
    if task is None or state.transition not in {"new", "continue", "resume"}:
        return state
    task.phase = ("failed" if terminal_action == "failed" else
                  "blocked" if terminal_action == "safe_stop" else
                  "awaiting_input" if terminal_action == "clarify" else
                  "awaiting_confirmation" if terminal_action == "proposal" else "responded")
    # A response is never labelled as business completion by this mechanism.
    data = content_data or {}
    for kind in ("artifact", "proposal"):
        if isinstance(data.get(kind), dict) and isinstance(data[kind].get("id"), str):
            setattr(task, kind + "_id", data[kind]["id"])
    if terminal_action in {"clarify", "safe_stop", "failed"} and not task.proposal_id:
        # Preserve typed goals even when compilation needs more information.
        # The capability compiler must still validate them before any proposal.
        goals = {(c.field_path, c.target_reference): c for c in task.unproposed_plan_changes}
        for change in changes or []:
            try:
                goal = PendingPlanChange.model_validate(change.model_dump())
                goals[(goal.field_path, goal.target_reference)] = goal
            except ValidationError:
                continue
        if len(goals) <= 12:
            task.unproposed_plan_changes = list(goals.values())
    if isinstance(data.get("proposal"), dict):
        task.unproposed_plan_changes = []
        task.pending_plan_changes = []
        task.pending_meal = None
        task.proposal_pending = False
        for change in changes or []:
            try:
                task.pending_plan_changes.append(PendingPlanChange.model_validate(change.model_dump()))
            except ValidationError:
                continue  # only bounded scalar plan updates can be carried over
    return state


def _preserves_existing_value(quote):
    # Here 修改/调整 is the object being retained, not an instruction to edit.
    # Match the entire source quote so an independent change is never erased.
    if re.fullmatch(r"(?:其他|其它|其余)(?:所有)?(?:修改|调整|改动|变更)(?:都)?(?:保持)?(?:不变|照旧)[。！!\s]*", quote):
        return True
    if re.fullmatch(r"(?:刚选的|之前的)?(?:两种|这些|原有)?状态(?:条件)?(?:都)?保留[。！!\s]*", quote):
        return True
    return bool(re.search(r"不变|照旧|保持原值|保留原值|保留原样|维持原样", quote)
                and not re.search(r"改|调整|更新|设置|增加|减少|不再|不要|取消|撤回|撤销|去掉|还原|恢复", quote))



def normalize_sourced_plan_resets(context, update, changes, message):
    """A numeric restoration is a replacement, only with exact field evidence."""
    if not update or update.action not in {'continue', 'resume'}:
        return update
    task = next((t for t in state_from_context(context).tasks if t.id == update.task_id and t.phase != 'cancelled'), None)
    if task is None:
        return update
    prior = task.pending_plan_changes if task.proposal_pending else task.unproposed_plan_changes if not task.proposal_id else []
    from app.services.agent_plan_completeness import _provided_value, _FIELDS
    targets = {c.target_reference for c in prior if c.target_reference}
    requirements = {r.key: r for r in task.requirements}
    result = []
    for edit in update.requirements:
        previous = requirements.get(edit.key)
        if edit.remove and previous and edit.quote in message and re.search(r'恢复|还原|重设', edit.quote):
            fields = {field for pattern, field in _FIELDS if re.search(pattern, edit.quote)}
            candidates = [c for c in changes if c.resource == 'workout_plan' and c.operation == 'update'
                          and c.preserve_unspecified and c.field_path in fields
                          and _provided_value(c, edit.quote, targets=targets)
                          and _provided_value(c, message, targets=targets)
                          and any(old.field_path == c.field_path and old.target_reference == c.target_reference
                                  and _provided_value(old, previous.quote, targets=targets) for old in prior)]
            if len(candidates) == 1:
                result.append(edit.model_copy(update={'remove': False}))
                continue
        result.append(edit)
    return update.model_copy(update={'requirements': result})


def _pure_deferred_gap(gap, quote):
    # This exception has no saved scalar goals to fall back on. Unknown or
    # mixed clauses must keep the original unresolved-requirements guard.
    labels = {
        "schedule.duration_weeks": "(?:周期|几周)",
        "schedule.days_per_week": "(?:每周训练天数|每周天数|每周训练几天|每周几天)",
        "exercise.sets": "(?:组数|几组)",
        "exercise.reps": "(?:次数|几次)",
        "exercise.rest_seconds": "(?:休息|休息时间|休息秒数|几秒)",
        "exercise.recommended_weight_kg": "(?:重量|几公斤|几千克)",
    }
    label = labels.get(gap.field_path)
    if not label or gap.quote != quote:
        return False
    target = rf"(?:{re.escape(gap.target_reference)}(?:的)?)?" if gap.target_reference else ""
    pending = (r"(?:待(?:我)?补(?:充)?|待定|未定|没(?:有)?定|"
               r"还没(?:确定|决定|定|给|提供|填|补(?:充)?|告诉)|"
               r"(?:稍后|下一句|之后|以后)(?:我)?(?:再)?(?:补(?:充)?|给|提供|决定|定|告诉)|"
               r"接着补(?:充)?|再告诉)(?:一下|你)?")
    return bool(re.fullmatch(
        rf"(?:请|先|把|将|调整|修改|当前|训练|计划|的|\s)*{target}{label}\s*{pending}\s*[。.!！]?", quote))


def _unproposed_requirement_resolved(requirement, task, update, changes, message):
    if requirement.quote in message:
        return True
    from app.services.agent_plan_completeness import fulfills_pending_requirement
    if not any(_pure_deferred_gap(g, requirement.quote) for g in task.plan_input_gaps):
        return False
    return any(edit.key == requirement.key and fulfills_pending_requirement(
        requirement.quote, edit.quote, task.plan_input_gaps, changes, message)
        for edit in update.requirements)


def preserve_pending_plan_changes(context, update, changes, *, withdrawals=(), message=""):
    """Merge an entire pending draft; withdrawals select fields, not DB rows."""
    if not update or update.action not in {"continue", "resume"}:
        if withdrawals:
            raise ValueError("task_plan_withdrawal_requires_pending_proposal")
        return changes
    task = next((task for task in state_from_context(context).tasks if task.id == update.task_id), None)
    prior_changes = []
    if task and task.phase != "cancelled":
        if task.proposal_pending:
            prior_changes = task.pending_plan_changes
        elif not task.proposal_id:
            prior_changes = task.unproposed_plan_changes
    if not prior_changes:
        if withdrawals:
            raise ValueError("task_plan_withdrawal_requires_pending_proposal")
        if (task and task.phase in {"awaiting_input", "failed", "blocked"}
                and not task.proposal_id and task.requirements
                and re.search(r"训练计划|当前计划", task.request)
                and any(c.resource == "workout_plan" for c in changes)
                and not all(_unproposed_requirement_resolved(r, task, update, changes, message)
                            for r in task.requirements)):
            raise ValueError("task_plan_unresolved_requirements")
        return changes
    if any(change.resource != "workout_plan" or change.operation != "update" or not change.preserve_unspecified for change in changes):
        if withdrawals:
            raise ValueError("task_plan_withdrawal_incompatible_changes")
        return changes
    from app.services.agent_plan_completeness import workflow_release, fulfills_pending_requirement
    previous_requirements = {r.key: r for r in task.requirements}
    def releases_workflow(edit):
        previous = previous_requirements.get(edit.key)
        return bool(previous and edit.quote in message and workflow_release(previous.quote, edit.quote)
                    and workflow_release(previous.quote, message))
    def fulfills_gap(edit):
        previous = previous_requirements.get(edit.key)
        return bool(previous and fulfills_pending_requirement(
            previous.quote, edit.quote, task.plan_input_gaps, changes, message))
    if any(edit.remove and not _preserves_existing_value(edit.quote)
           and not releases_workflow(edit) and not fulfills_gap(edit)
           for edit in update.requirements) and not withdrawals:
        raise ValueError("task_plan_withdrawal_fields_required")
    if not changes and not withdrawals:
        return changes
    from app.services.agent_intent import ChangeRequest
    merged = {(change.field_path, change.target_reference): ChangeRequest.model_validate(change.model_dump())
              for change in prior_changes}
    withdrawn = set()
    replacement_keys = {(c.field_path, c.target_reference) for c in changes}
    for item in withdrawals:
        key = (item.field_path, item.target_reference)
        if not item.quote.strip() or item.quote not in message:
            raise ValueError("task_plan_withdrawal_not_current_user_instruction")
        if _preserves_existing_value(item.quote):
            continue
        has_withdrawal = re.search(r"不再|不要|取消|撤回|撤销|去掉|还原|恢复", item.quote)
        if not has_withdrawal and key in replacement_keys and re.search(r"改|调整|更新|设置", item.quote):
            continue  # overwriting this field already replaces its old value
        if not has_withdrawal:
            raise ValueError("task_plan_withdrawal_not_current_user_instruction")
        missing_goal_keys = {(g.field_path, g.target_reference) for g in task.plan_input_gaps}
        if (key not in merged and key not in missing_goal_keys) or key in withdrawn:
            raise ValueError("task_plan_withdrawal_unknown_or_duplicate_field")
        withdrawn.add(key)
    if withdrawn & {(c.field_path, c.target_reference) for c in changes}:
        raise ValueError("task_plan_withdrawal_conflicts_with_update")
    for key in withdrawn:
        merged.pop(key, None)
    merged.update({(change.field_path, change.target_reference): change for change in changes})
    if len(merged) > 12:
        raise ValueError("task_pending_change_capacity_exceeded")
    return list(merged.values())


def revise_pending_meal(context, update, changes):
    """Only a live, owned create draft may be revised into another create draft."""
    if not update or update.action not in {"continue", "resume"} or len(changes) != 1:
        return changes
    task = next((task for task in state_from_context(context).tasks if task.id == update.task_id), None)
    if not task or not task.proposal_pending or task.phase == "cancelled" or task.pending_meal is None:
        return changes
    change = changes[0]
    if change.resource != "nutrition" or change.operation not in {"create", "update"} or not change.preserve_unspecified:
        return changes
    value = task.pending_meal.model_dump(mode="json")
    if change.field_path in {"meal", "meal_log"} and isinstance(change.value, dict):
        # A full items list replaces the previous list; deleted food stays deleted.
        if set(change.value) - {"logged_at", "meal_type", "items"}:
            return changes
        value.update(change.value)
    elif change.field_path in {"meal.meal_type", "meal.logged_at", "meal.items"} and change.operation == "update":
        value[change.field_path.split(".", 1)[1]] = change.value
    else:
        return changes
    return [change.model_copy(update={"operation": "create", "field_path": "meal", "target_reference": None, "value": value})]


def _pending_meal(payload):
    if not isinstance(payload, dict) or payload.get("proposal_type") != "meal_log_create_v1":
        return None
    try:
        after = payload["after"]
        return PendingMealDraft.model_validate({
            "logged_at": after["logged_at"], "meal_type": after["meal_type"],
            "items": [{key: item[key] for key in ("food_id", "food_name", "amount_g")}
                      for item in after["items"]],
        })
    except (KeyError, TypeError, ValidationError):
        return None


async def load_task_snapshot(db, before_run) -> TaskSnapshot:
    row = await db.scalar(select(AgentRun.execution_trace).where(
        AgentRun.user_id == before_run.user_id,
        AgentRun.conversation_id == before_run.conversation_id,
        AgentRun.status == "completed",
        AgentRun.execution_trace["task_state"].is_not(None),
        precedes(AgentRun, before_run),
    ).order_by(AgentRun.queue_position.desc().nulls_last(), AgentRun.queued_at.desc(), AgentRun.id.desc()).limit(1))
    state = parse_snapshot(row.get("task_state") if isinstance(row, dict) else None)
    ids = [task.proposal_id for task in state.tasks if task.proposal_id]
    pending = {}
    if ids:
        proposals = (await db.scalars(select(AgentProposal).where(
            AgentProposal.id.in_(ids), AgentProposal.user_id == before_run.user_id,
            AgentProposal.conversation_id == before_run.conversation_id,
            AgentProposal.status == "pending_confirmation",
            AgentProposal.expires_at > datetime.now(timezone.utc),
        ))).all()
        pending = {proposal.id: proposal for proposal in proposals}
    for task in state.tasks:
        task.proposal_pending = task.proposal_id in pending
        task.pending_meal = _pending_meal(pending[task.proposal_id].payload_data) if task.proposal_pending else None
        if not task.proposal_pending:
            task.pending_plan_changes = []
    return state
