import copy
import pytest
from verify_usage_v2 import check

def fixture():
    response={'http_status':200,'input_tokens':12,'output_tokens':5,'cached_input_tokens':None}
    call={'sequence':1,'source':'structured','outcome':'http_success','transport_boundary_observed':True,'transport_attempts':1,'transport_errors':0,'responses':[response],**response}
    usage={'version':'transport_boundary_v2','scope':'worker_attempt','worker_attempt':1,'prior_attempts_unobserved':False,'observation_complete':True,
           'client_attempts':1,'transport_attempts':1,'http_requests':1,'http_responses':1,'http_successes':1,'http_errors':0,
           'transport_errors':0,'local_errors':0,'unclassified_client_errors':0,'pending_or_cancelled':0,'usage_known_requests':1,
           'known_input_tokens':12,'known_output_tokens':5,'input_tokens':12,'output_tokens':5,'calls':[call]}
    body={'model_usage':usage,'attempt_count':1,'input_tokens':12,'output_tokens':5}
    providers=[{'complete':True,'lifecycle_complete':True,'status':200,'response':{'usage':{'prompt_tokens':12,'completion_tokens':5}}}]
    return body,copy.deepcopy(body),providers,[]

def test_complete_positive():assert check(*fixture())['full_usage_known']

def test_missing_usage_and_proven_local_guard_are_not_zero_or_transport_error():
    b,s,p,g=fixture();u=b['model_usage'];call=u['calls'][0]
    p[0]['status']=503;p[0]['response']={'error':'busy'}
    response=dict(http_status=503,input_tokens=None,output_tokens=None,cached_input_tokens=None)
    call.update(response);call.update(outcome='http_error',responses=[response])
    u['calls'].append(dict(sequence=2,source='structured',outcome='local_error',transport_boundary_observed=True,transport_attempts=0,transport_errors=0,responses=[],http_status=None,input_tokens=None,output_tokens=None,cached_input_tokens=None))
    u.update(client_attempts=2,local_errors=1,http_successes=0,http_errors=1,usage_known_requests=0,known_input_tokens=0,known_output_tokens=0,input_tokens=None,output_tokens=None)
    b.update(input_tokens=None,output_tokens=None);g.append({'stage':'before_provider_send'})
    assert not check(b,copy.deepcopy(b),p,g)['full_usage_known']

@pytest.mark.parametrize('change',['request_count','response_count','clients','transfer','success','http_error','local','transport','unknown','pending',
                                'cache','response_tokens','top_tokens','sql','worker','missing_guard','raw_timeout','raw_incomplete','sequence','outcome','legacy_version'])
def test_mismatched_or_unproven_evidence_rejected(change):
    b,s,p,g=fixture();u=b['model_usage'];call=u['calls'][0]
    names={'request_count':'http_requests','response_count':'http_responses','clients':'client_attempts','transfer':'transport_attempts','success':'http_successes','http_error':'http_errors','local':'local_errors','transport':'transport_errors','unknown':'unclassified_client_errors','pending':'pending_or_cancelled'}
    if change in names:u[names[change]]+=1
    elif change=='cache':call['responses'][0]['cached_input_tokens']=10
    elif change=='response_tokens':call['responses'][0]['input_tokens']=13
    elif change=='top_tokens':b['input_tokens']=99
    elif change=='sql':s['output_tokens']=6
    elif change=='worker':b['attempt_count']=2
    elif change=='missing_guard':g.append({'stage':'before_provider_send'})
    elif change=='raw_timeout':p[0]['error_type']='ReadTimeout'
    elif change=='raw_incomplete':p[0]['complete']=False
    elif change=='sequence':call['sequence']=2
    elif change=='outcome':call['outcome']='local_error'
    else:u['version']='provider_http_v1'
    if change!='sql':s=copy.deepcopy(b)
    with pytest.raises(AssertionError):check(b,s,p,g)
