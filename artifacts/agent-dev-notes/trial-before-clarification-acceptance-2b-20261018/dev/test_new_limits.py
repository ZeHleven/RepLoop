import json
from pathlib import Path
import pytest
from continuation_budget import ContinuationBudget,EvaluationStopped

def make(tmp_path,limit=5):
    c=json.loads((Path(__file__).parent/'continuation.json').read_text(encoding='utf8'))
    c['approved_limits']['max_estimated_cny']=limit
    c['max_combined_estimated_cny']=limit
    p=tmp_path/'continuation.json';p.write_text(json.dumps(c))
    return ContinuationBudget(tmp_path/'budget.json','frozen',seconds=2400,runs=60,requests=400,max_estimated_cny=limit,wall=lambda:100.,mono=lambda:50.,epoch=lambda:'test',continuation_path=p)

@pytest.mark.parametrize('limit',[1,4])
def test_pair_split_stops_before_request_above_assigned_limit(tmp_path,limit):
    b=make(tmp_path,limit)
    raw=json.dumps(dict(model='deepseek-v4-flash',messages=[dict(role='user',content='synthetic')],max_tokens=limit*62501))
    b.reserve_request(raw)
    with pytest.raises(EvaluationStopped,match='reservation_limit'):b.reserve_request(raw)
    assert b.state['requests']==1

def test_400_requests_and_cost_above_old_limit_with_known_usage(tmp_path):
    b=make(tmp_path);raw=json.dumps(dict(model='deepseek-v4-flash',messages=[dict(role='user',content='synthetic')],max_tokens=1000))
    for i in range(1,401):
        assert b.reserve_request(raw)==i
        b.record_provider_cost(dict(index=i,request_raw=raw,complete=True,lifecycle_complete=True,status=200,response={'usage':dict(prompt_tokens=1000,completion_tokens=1000)}))
    assert b.state['estimated_cost_units']==400_000_000
    with pytest.raises(EvaluationStopped):b.reserve_request(raw)

def test_60_runs_and_no_61st(tmp_path):
    b=make(tmp_path)
    for i in range(1,61):assert b.reserve('runs')==i
    with pytest.raises(EvaluationStopped):b.reserve('runs')

def test_five_yuan_reservation_limit(tmp_path):
    b=make(tmp_path);raw=json.dumps(dict(model='deepseek-v4-flash',messages=[dict(role='user',content='synthetic')],max_tokens=384000))
    b.reserve_request(raw)
    with pytest.raises(EvaluationStopped,match='reservation_limit'):b.reserve_request(raw)
    assert b.state['requests']==1
