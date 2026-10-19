"""Compile count-only, conditional return steps from the saved plan.

Steps are examples for the allocated temporary schedule. They are not medical
clearance, an automatic exercise prescription, or evidence that the user has
performed any step. Alternative temporary choices need their own recomputation.
"""
import copy


RETURN_LOAD_CONTRACT='''
回归组次状态：逐动作维护实际组数和每组次数，从回答最终选定的临时方案开始，每一步只推进明确调整的动作；只有每个相关动作都达到原组数时，才说全部恢复。原计划的组次是本次回归上限，已是原次数就保持，不再从该次数往上加；原次数为范围时用上界约束。不同临时选项分别核算，不混用示例或其他分支的状态；尚未恢复的动作继续说明缺口和下一步前提，不能删掉用户的回归目标。
回归负重起点：负重不变不是沿用档案公斤数。count_steps的reps_and_weight_unchanged只表示同一已选定且耐受的回归起步方案在加组时不同时改次数和负重，weight_unchanged_from说明其基准；数值示例没有给出个人起步公斤数。先依据症状、功能和已有耐受信息确定起步方式，未明确时不默认原处方重量；用户已经提供的起点继续使用，不重复追问。
组数、次数、负重各阶段须承接同一实际方案：不能前一步要求负重保持原样且没有其他起点，后一步又说最后才试负重。可保持已选定的轻量或明确起步负重，待反馈允许再小幅调整；显式改变过起点则从改变后的步骤续接。把未指定重量与零负重分开，不把本示例或档案数字当医学许可。
'''


def recovery_bounds(schedule, allocation):
    originals = {x['source_index']:(s['day'], x) for s in schedule['sessions'] for x in s['exercises']}
    current = {x['source_index']:x['sets'] for s in allocation['sessions'] for x in s['exercises']}
    included_days = {s['original_day'] for s in allocation['sessions']}
    rows = []
    for index, (day, ex) in originals.items():
        rows.append({'source_index':index, 'source_path':f'#/plan/exercises/{index}', 'original_day':day,
                     'temporary_sets':current.get(index, 0), 'ceiling_sets':ex['sets'],
                     'original_reps_range':[ex['reps_low'], ex['reps_high']],
                     'return_reps':min(ex['reps_low'], 8), 'rest_seconds':ex['rest_seconds'],
                     'day_in_temporary_allocation':day in included_days})
    states = copy.deepcopy(current); steps = []
    # Restore only already retained days. Reintroducing a day changes frequency
    # and needs a separate decision after recovery and a new time calculation.
    while True:
        candidates = [r for r in rows if r['day_in_temporary_allocation'] and states.get(r['source_index'], 0) < r['ceiling_sets']]
        if not candidates:
            break
        advanced = False
        for row in candidates:
            index = row['source_index']; proposed = dict(states); proposed[index] = proposed.get(index, 0)+1
            estimates = {day:240+sum(proposed.get(r['source_index'], 0)*(4*r['return_reps']+r['rest_seconds']) for r in rows if r['original_day']==day)
                         for day in included_days}
            cap = allocation['session_cap_minutes']
            if cap is not None and any(seconds > cap*60 for seconds in estimates.values()):
                continue
            steps.append({'changed_source_index':index, 'from_sets':states.get(index, 0), 'to_sets':proposed[index],
                          'weekly_sets':sum(proposed.values()), 'days_per_week':len(included_days),
                          'estimated_session_seconds':estimates,
                          'conditions_required':['symptoms_and_daily_function_improved', 'previous_step_and_next_day_tolerated', 'initial_return_load_selected_and_tolerated'],
                          'reps_and_weight_unchanged':True,
                          'weight_unchanged_from':'previous_individually_selected_tolerated_step'})
            states = proposed; advanced = True
        if not advanced:
            break
    pending = [r['source_index'] for r in rows if states.get(r['source_index'], 0) < r['ceiling_sets']]
    return {'version':1, 'basis':'conditional_example_from_allocated_schedule',
            'baseline_source':'active_plan_not_profile_preference',
            'weekly_set_ceiling':schedule['weekly_sets'], 'days_per_week_ceiling':schedule['days_per_week'],
            'row_bounds':rows, 'count_steps':steps, 'remaining_source_indices':pending,
            'remaining_requires_new_time_frequency_and_symptom_check':bool(pending),
            'different_temporary_selection_requires_recalculation':True,
            'each_step_requires_actual_recovery_feedback':True, 'calendar_or_session_count_does_not_authorize_progress':True,
            'stages':['sets_one_source_row_one_set_at_a_time', 'repetitions_after_set_tolerance', 'load_after_repetition_tolerance'],
            'example_initial_load_kg':None, 'initial_load_requires_separate_selection':True,
            'saved_weight_is_mandatory_target':False, 'medical_clearance':False}
