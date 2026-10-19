"""New execution window with preserved prior failures and conservative reserves.

Reservations are estimates, never token measurements or invoice facts. Missing
usage still stops this window. The original stopped ledger is read-only.
"""
import hashlib,json
from pathlib import Path

from durable_budget import Budget,EvaluationStopped,atomic_json,file_lock
from cost_budget import usage_cost_units

ROOT=Path(__file__).resolve().parent


def request_reservation(raw):
    if isinstance(raw,str):raw=raw.encode('utf-8')
    body=json.loads(raw)
    if not isinstance(body,dict):raise ValueError('invalid_request_object')
    if body.get('model')!='deepseek-v4-flash':raise ValueError('unapproved_model')
    output=body.get('max_tokens',body.get('max_completion_tokens'))
    if 'max_tokens' in body and 'max_completion_tokens' in body and body['max_tokens']!=body['max_completion_tokens']:raise ValueError('conflicting_output_limits')
    if type(output) is not int or not 0<output<=384000:raise ValueError('missing_or_invalid_output_limit')
    if not isinstance(body.get('messages'),list) or not body['messages']:raise ValueError('missing_messages')
    # Text-only fixture requests. Reject multimodal payloads whose accounting
    # cannot be estimated from this frozen text-only policy.
    if any(not isinstance(m,dict) or isinstance(m.get('content'),list) for m in body['messages']):raise ValueError('non_text_request')
    incoming=2*len(raw)+8192
    if incoming>1_000_000:raise ValueError('reservation_input_limit')
    return {'request_sha256':hashlib.sha256(raw).hexdigest(),'request_bytes':len(raw),'reserved_input_tokens':incoming,
            'output_token_limit':output,'units':incoming*200+output*800,'status':'pending'}


class ContinuationBudget(Budget):
    def __init__(self,*args,continuation_path=None,**kwargs):
        self.continuation_path=Path(continuation_path or ROOT/'continuation.json')
        raw=self.continuation_path.read_bytes();self.continuation=json.loads(raw)
        self.continuation_hash=hashlib.sha256(raw).hexdigest()
        for rel,sha in self.continuation['source_hashes'].items():
            if hashlib.sha256((self.continuation_path.parent/rel).read_bytes()).hexdigest()!=sha:raise EvaluationStopped('prior_evidence_changed')
        super().__init__(*args,**kwargs)
        with file_lock(str(self.path)+'.lock'):
            self._load();s=self.state
            if 'continuation_sha256' not in s:
                if s['requests'] or s['runs'] or s['instances']:raise EvaluationStopped('cannot_adopt_used_budget')
                s.update(continuation_sha256=self.continuation_hash,carried_failed_instances=self.continuation['carried_failed_instances'],
                         prior_requests=self.continuation['prior_requests'],prior_runs=self.continuation['prior_runs'],
                         prior_actual_cost_cny=None,carried_reserve_units=self.continuation['carried_reserve_units'],request_reservations={})
            elif s['continuation_sha256']!=self.continuation_hash:raise EvaluationStopped('continuation_contract_changed')
            approved=self.continuation.get('approved_limits',dict(requests=250,runs=40,max_estimated_cny=3))
            if self.limits['requests']+s['prior_requests']!=approved['requests'] or self.limits['runs']+s['prior_runs']!=approved['runs'] or self.limits.get('max_estimated_cny')!=approved['max_estimated_cny']:
                raise EvaluationStopped('authorization_limits_changed')
            if any(r['status']=='pending' for r in s['request_reservations'].values()):self._set_stop('unresolved_provider_reservation')
            self._account();atomic_json(self.path,s)

    def _account(self):
        s=self.state;reservations=s['request_reservations'];known=s.get('provider_cost_units',{})
        s['estimated_cost_units']=sum(known.values())
        s['committed_estimate_units']=s['carried_reserve_units']+sum(known[k] if r['status']=='known' else r['units'] for k,r in reservations.items())
        if s['committed_estimate_units']>self.limits['max_estimated_cny']*100_000_000:self._set_stop('estimated_cost_budget')

    def begin(self,instance):
        if instance in self.continuation['carried_failed_instances']:raise EvaluationStopped('prior_started_instance_no_replay')
        return super().begin(instance)

    def reserve(self,kind):
        if kind=='requests':raise EvaluationStopped('request_reservation_requires_payload')
        return super().reserve(kind)

    def reserve_request(self,raw):
        try:reservation=request_reservation(raw)
        except (ValueError,TypeError,KeyError) as exc:
            self.stop('request_estimate_invalid');raise EvaluationStopped('request_estimate_invalid') from exc
        with file_lock(str(self.path)+'.lock'):
            self._load();self._check(counters=('requests',));s=self.state;self._account()
            if s['committed_estimate_units']+reservation['units']>self.limits['max_estimated_cny']*100_000_000:
                self._set_stop('estimated_cost_reservation_limit');atomic_json(self.path,s);raise EvaluationStopped(s['stop'])
            s['requests']+=1;index=s['requests'];s['request_reservations'][str(index)]=reservation
            self._account();atomic_json(self.path,s);return index

    def record_provider_cost(self,row):
        with file_lock(str(self.path)+'.lock'):
            self._load();s=self.state;index=row.get('index');reservation=s['request_reservations'].get(str(index))
            try:
                if type(index) is not int or reservation is None:raise ValueError('unreserved_request')
                if hashlib.sha256(row['request_raw'].encode('utf-8')).hexdigest()!=reservation['request_sha256']:raise ValueError('reserved_request_mismatch')
                units=usage_cost_units(row);usage=row['response']['usage']
                if units>reservation['units'] or usage['prompt_tokens']>reservation['reserved_input_tokens'] or usage['completion_tokens']>reservation['output_token_limit']:
                    self._set_stop('provider_usage_exceeded_reservation')
                known=s.setdefault('provider_cost_units',{})
                if str(index) in known and known[str(index)]!=units:raise ValueError('usage_conflict')
                known[str(index)]=units;reservation['status']='known'
            except (ValueError,KeyError,TypeError) as exc:
                if reservation:reservation['status']='unknown'
                self._set_stop('provider_cost_unknown',details={'provider_index':index,'reason':str(exc)})
                uncertainty={'index':index,'reason':str(exc)}
                if uncertainty not in s.setdefault('cost_uncertainties',[]):s['cost_uncertainties'].append(uncertainty)
            self._account();atomic_json(self.path,s)
