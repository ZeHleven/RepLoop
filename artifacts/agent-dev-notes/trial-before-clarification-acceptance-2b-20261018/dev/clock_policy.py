"""Version 2 execution clock: fixed UTC + cumulative monotonic deadlines."""
import math


def finite(value):return type(value) in (int,float) and math.isfinite(value)


def inspect_v2(state,*,freeze,limits,clock,gap=30):
    reasons=[];metrics=None
    if state.get('freeze')!=freeze or state.get('limits')!=limits:reasons.append('contract_mismatch')
    if state.get('stop'):reasons.append('sticky_stop:'+str(state['stop']))
    if clock.source not in ('monotonic','CLOCK_MONOTONIC_RAW') or state.get('clock_policy')!={'version':2,'elapsed_clock':clock.source,'max_wall_adjustment_seconds':gap}:reasons.append('clock_policy_mismatch')
    keys=['started','deadline','started_mono','deadline_mono','last_wall','last_mono','runs','requests']
    valid=all(finite(state.get(k)) for k in keys) and all(finite(v) for v in [clock.wall,clock.mono])
    valid_limits=all(type(limits.get(k)) is int and limits[k]>0 for k in ['seconds','runs','requests'])
    if 'max_estimated_cny' in limits:valid_limits=valid_limits and type(limits['max_estimated_cny']) is int and limits['max_estimated_cny']>0
    if not valid or not valid_limits or not finite(gap) or gap<0:reasons.append('invalid_budget_or_clock')
    else:
        if state['deadline']!=state['started']+limits['seconds'] or state['deadline_mono']!=state['started_mono']+limits['seconds']:reasons.append('deadline_contract_mismatch')
        if clock.wall>=state['deadline']:reasons.append('wall_deadline')
        if clock.mono>=state['deadline_mono']:reasons.append('monotonic_deadline')
        for k in ['runs','requests']:
            if type(state[k]) is not int or state[k]<0:reasons.append('invalid_'+k)
            elif state[k]>=limits[k]:reasons.append(k+'_budget')
        if not state.get('clock_epoch') or not clock.epoch:reasons.append('clock_epoch_unknown')
        elif state['clock_epoch']!=clock.epoch:reasons.append('clock_epoch_changed')
        if state['started_mono']<0 or state['last_mono']<state['started_mono']:reasons.append('invalid_clock_history')
        if clock.mono<state['last_mono']:reasons.append('monotonic_regression')
        dw,dm=clock.wall-state['last_wall'],clock.mono-state['last_mono']
        elapsed=clock.mono-state['started_mono'];drift=(clock.wall-state['started'])-elapsed
        prior_drift=(state['last_wall']-state['started'])-(state['last_mono']-state['started_mono'])
        if abs(prior_drift)>gap:reasons.append('invalid_clock_history')
        if abs(drift)>gap:reasons.append('cumulative_clock_discontinuity')
        metrics=dict(wall_delta_seconds=dw,monotonic_delta_seconds=dm,step_difference_seconds=dw-dm,
                     cumulative_wall_minus_monotonic_seconds=drift,monotonic_elapsed_seconds=elapsed,
                     monotonic_remaining_seconds=state['deadline_mono']-clock.mono,
                     wall_adjusted=dw<0 or abs(dw-dm)>.05)
    if not isinstance(state.get('instances'),dict) or any(v not in ('started','finished') for v in state.get('instances',{}).values()):reasons.append('invalid_instances')
    return dict(read_only_allowed=True,execution_allowed=not reasons,reasons=list(dict.fromkeys(reasons)),
                deadline=state.get('deadline'),deadline_mono=state.get('deadline_mono'),runs=state.get('runs'),requests=state.get('requests'),counters_reserved=False,clock_metrics=metrics)
