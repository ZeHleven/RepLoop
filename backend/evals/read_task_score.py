"""Mechanical read-task checks, separate from full business review."""
from evals.tool_attempt_ledger import tool_attempt_ledger

def score_read_task(case,rec,*,business_date):
    errors=[]
    ledger=tool_attempt_ledger(rec)
    if not ledger['complete']:errors.append('tool_ledger_incomplete')
    if rec.get('error'):errors.append('execution_error')
    if not rec.get('turns'):errors.append('missing_turn')
    if not rec.get('baseline') or rec.get('final')!=rec.get('baseline'):errors.append('business_changed_or_missing')
    for t in rec.get('turns',[]):
        b=t['body'];trace=b.get('execution_trace') or {}
        if b.get('status')!='completed' or not b.get('reply'):errors.append('no_answer')
        if b.get('error_code') or trace.get('termination_reason') in ('required_evidence_missing','intent_understanding_unavailable'):errors.append('blocked_delivery')
        if b.get('proposal') or t.get('artifact'):errors.append('unexpected_proposal_or_artifact')
    tools={t['tool'] for t in rec.get('tool_attempts',[]) if t.get('status')=='success'}
    for t in case['expected_tools']:
        if t not in tools:errors.append('missing_tool:'+t)
    if case['id']!='NA10' and case.get('requires_nutrition',True) and not any(t.startswith('nutrition.') for t in tools):errors.append('missing_nutrition_evidence')
    if not case.get('requires_nutrition',True) and not any(t.startswith('workout.') or t.startswith('plan.') for t in tools):errors.append('missing_workout_evidence')
    if ledger['errors'] or any(t.get('status')!='success' or not t.get('lifecycle_complete') for t in rec.get('tool_attempts',[])):errors.append('tool_error')
    if not rec.get('provider') or any(p.get('status')!=200 or not p.get('lifecycle_complete') for p in rec.get('provider',[])):errors.append('provider_or_trace_error')
    eligible=list(errors);calibrations=[]
    # Keep strict original diagnostics. Recovery changes the final task state,
    # never the error numerator, number of attempts or their cost.
    if 'tool_error' in eligible:
        # Final business review judges recovery and all required evidence.
        # Do not mechanically fail an otherwise eligible answer because an
        # unnecessary extra call failed after required facts were obtained.
        if ledger['complete'] and not any(e.startswith('missing_') or e in {'blocked_delivery','no_answer','execution_error'} for e in eligible):
            eligible.remove('tool_error');calibrations.append('E59-1: terminal answer eligible for business review; failed attempts retained')
    if 'provider_or_trace_error' in eligible:
        providers=rec.get('provider',[])
        if providers and all(p.get('lifecycle_complete') for p in providers) and providers[-1].get('status')==200:
            eligible.remove('provider_or_trace_error');calibrations.append('E58-2: recovered provider error')
    if 'missing_tool:profile.get_summary' in eligible and 'saved_weight_72' in case.get('required',[]):
        observations=[t.get('result') or {} for t in rec.get('tool_attempts',[]) if t['tool']=='weight.list_history' and t.get('status')=='success' and t.get('lifecycle_complete')]
        if any(d.get('scope',{}).get('as_of')==business_date and d.get('scope',{}).get('selection')=='latest_records' and d.get('records') and d['records'][0].get('weight_kg')==72 for d in observations):
            eligible.remove('missing_tool:profile.get_summary');calibrations.append('E58-1: saved weight supported by current weight history')
    result = {'structural_pass':not errors,'errors':errors,'terminal_delivery_eligible':not eligible,'terminal_errors':eligible,'calibrations':calibrations,'tool_ledger':ledger,'semantic_pass':None,'answer_accuracy':None,'hallucination_rate':None}
    if rec.get('attempt_observation_version') is not None:
        from evals.attempt_metrics import summarize_attempts
        result['execution_metrics'] = summarize_attempts(rec, prices=rec.get('frozen_model_prices'))
        if not result['execution_metrics']['evidence_valid']:
            result['structural_pass'] = result['terminal_delivery_eligible'] = False
            result['errors'] = [*result['errors'], 'attempt_observation_invalid']
            result['terminal_errors'] = [*result['terminal_errors'], 'attempt_observation_invalid']
    return result
