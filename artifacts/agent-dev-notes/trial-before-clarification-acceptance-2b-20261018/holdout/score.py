"""R51 mechanical checks only. Full semantic/claim review remains separate."""
import json
from evals.tool_attempt_ledger import tool_attempt_ledger
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BENCH=json.loads((ROOT/'benchmark.json').read_text(encoding='utf8'))
NUTRIENTS=('calories','protein_g','carbs_g','fat_g')
FOODS={f['name']:f for f in BENCH['foods']}
def proposal_errors(*args):raise AssertionError('R51 has no live write test tasks')
from evals.read_task_score import score_read_task

def score(case,rec):
    result=score_read_task(case,rec,business_date=BENCH['business_date'])
    if case.get('urgent_without_tool_requirement'):
        # Immediate emergency guidance must not be delayed for irrelevant historical tools.
        result['terminal_errors']=[e for e in result['terminal_errors'] if e not in ('missing_workout_evidence','missing_nutrition_evidence')]
        result['terminal_delivery_eligible']=not result['terminal_errors']
        result['calibrations'].append('Pre-frozen emergency task: no historical tool required')
    return result

def selftest():
    assert len({c['id'] for c in BENCH['cases']})==len(BENCH['cases']), 'Duplicate case ID'
    for case in BENCH['cases']:
        for metric,target in case.get('target_comparison',{}).items():
            assert len(case['expected_rows'])==1
            assert abs(target['recorded']-case['expected_rows'][0][metric])<1e-6,case['id']
            assert abs(target['recorded']-target['target']-target['difference'])<1e-6,case['id']
    c={'expected_tools':['profile.get_summary'],'id':'NA01'}
    assert not score(c,{})['structural_pass']
    c={'expected_tools':['plan.get_active'],'requires_nutrition':False,'id':'probe'}
    rec={'baseline':{'synthetic':1},'final':{'synthetic':1},'turns':[{'body':{'id':'selftest','status':'completed','reply':'needs semantic review','execution_trace':{'actions':[],'observations':[]}}}],
         'tool_attempts':[],'tool_audits':[],
         'provider':[{'status':400,'lifecycle_complete':True},{'status':200,'lifecycle_complete':True}]}
    trace=rec['turns'][0]['body']['execution_trace']
    for i,status in enumerate(['error','success']):
        cid=f'probe-{i}';args={'probe':i};state='completed' if status=='success' else 'failed'
        rec['tool_attempts'].append({'attempt_id':cid,'tool':'plan.get_active','arguments':args,'status':status,'lifecycle_complete':True})
        trace['actions'].append({'sequence':2*i+1,'call_id':cid,'tool_id':'plan.get_active','arguments':args,'status':state})
        trace['observations'].append({'action_sequence':2*i+1,'call_id':cid,'tool_id':'plan.get_active','status':status,'summary':{}})
        rec['tool_audits'].append({'id':cid,'run_id':'selftest','call_id':cid,'tool_name':'plan.get_active','arguments_data':args,'result_data':{},'status':state})
    s=score(c,rec);assert not s['structural_pass'] and s['terminal_delivery_eligible'] and len(s['calibrations'])==2
    rec['tool_attempts'].pop();assert not score(c,rec)['terminal_delivery_eligible']
    rec['tool_attempts'].append({'tool':'plan.get_active','status':'success','lifecycle_complete':False});assert not score(c,rec)['terminal_delivery_eligible']
    return {'passed':True,'meaning':'original diagnostics retained; terminal recovery calibrated; semantics still reviewed separately'}
