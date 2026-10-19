"""Locked, boot-aware execution budget. Read-only recovery never uses this writer."""
import json
import os
import time
from clock_policy import finite
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from session_recovery import Clock, clock_now, inspect_budget


class EvaluationStopped(RuntimeError):pass


@contextmanager
def file_lock(path, *, blocking=True):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        if os.name=='nt':
            import msvcrt
            if f.tell()==0:f.write(b'0');f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(),msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK,1)
            try:yield
            finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
            try:yield
            finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def atomic_json(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.tmp-'+uuid4().hex)
    with tmp.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,default=str,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    if os.name!='nt':
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)


class Budget:
    def __init__(self,path,freeze,*,seconds=4200,runs=350,requests=1400,max_estimated_cny=None,wall=time.time,mono=None,mono_source=None,gap=30,epoch=None):
        from execution_clock import DEFAULT_SOURCE,counter
        self.mono_source=mono_source or ('monotonic' if mono is not None else DEFAULT_SOURCE)
        mono=mono or counter(self.mono_source)
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.wall=wall;self.mono=mono;self.gap=gap;self.epoch=epoch or (lambda:clock_now().epoch)
        self.freeze=freeze;self.limits=dict(seconds=seconds,runs=runs,requests=requests)
        if max_estimated_cny is not None:
            if type(max_estimated_cny) is not int or max_estimated_cny<=0:raise ValueError('Invalid estimated cost limit')
            self.limits['max_estimated_cny']=max_estimated_cny
        with file_lock(str(self.path)+'.lock'):
            if self.path.exists():self._load()
            else:
                clock,span=self._sample()
                if not finite(clock.wall) or not finite(clock.mono) or clock.mono<0 or not finite(span) or span<0:raise EvaluationStopped('Invalid initial clock')
                self.state=dict(budget_version=2,freeze=freeze,limits=self.limits,started=clock.wall,deadline=clock.wall+seconds,
                    started_mono=clock.mono,deadline_mono=clock.mono+seconds,
                    runs=0,requests=0,instances={},identities={},last_wall=clock.wall,last_mono=clock.mono,clock_epoch=clock.epoch,stop=None,
                    clock_policy=dict(version=2,elapsed_clock=self.mono_source,max_wall_adjustment_seconds=gap),clock_checks=0,
                    recent_clock_samples=[],clock_adjustments=[],clock_adjustment_count=0)
                atomic_json(self.path,self.state)

    def _load(self):
        def invalid_constant(value):raise EvaluationStopped('Invalid budget numeric constant: '+value)
        self.state=json.loads(self.path.read_text(encoding='utf-8'),parse_constant=invalid_constant)
        if self.state.get('freeze')!=self.freeze or self.state.get('limits')!=self.limits:
            raise EvaluationStopped('Resume contract mismatch')
        if self.state.get('budget_version')!=2:raise EvaluationStopped('Legacy budget requires read-only inspection; no automatic upgrade')
        if self.state.get('clock_policy')!=dict(version=2,elapsed_clock=self.mono_source,max_wall_adjustment_seconds=self.gap):raise EvaluationStopped('Clock policy mismatch')

    def _sample(self):
        before=self.mono();w=self.wall();after=self.mono()
        return Clock(w,after,self.epoch(),self.mono_source),after-before

    @staticmethod
    def _safe(value):
        if isinstance(value,float) and not finite(value):return 'non_finite:'+repr(value)
        if isinstance(value,dict):return {k:Budget._safe(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [Budget._safe(v) for v in value]
        return value

    def _set_stop(self,reason,*,details=None,clock=None,span=None):
        if self.state.get('stop'):return False
        if clock is None:clock,span=self._sample()
        s=self.state;s['stop']=reason
        s['stop_event']=self._safe(dict(reason=reason,previous={k:s.get(k) for k in ['last_wall','last_mono','clock_epoch','started','started_mono','deadline','deadline_mono','runs','requests']},
            current=dict(wall=clock.wall,mono=clock.mono,epoch=clock.epoch,source=clock.source,sampling_span_seconds=span),details=details))
        return True

    def _check(self, *, counters=()):
        s=self.state
        if s.get('stop'):raise EvaluationStopped(s['stop'])
        clock,span=self._sample()
        decision=inspect_budget(s,freeze=self.freeze,limits=self.limits,clock=clock,gap=self.gap)
        reasons=[v for v in decision['reasons'] if v not in {'runs_budget','requests_budget'} or v.removesuffix('_budget') in counters]
        if not finite(span) or span<0:reasons.append('invalid_sampling_span')
        s['clock_checks']+=1
        sample=self._safe(dict(check=s['clock_checks'],wall=clock.wall,mono=clock.mono,epoch=clock.epoch,sampling_span_seconds=span,metrics=decision.get('clock_metrics'),reasons=reasons,counters=list(counters)))
        s['recent_clock_samples']=(s['recent_clock_samples']+[sample])[-8:]
        if (decision.get('clock_metrics') or {}).get('wall_adjusted'):
            s['clock_adjustment_count']+=1
            if len(s['clock_adjustments'])<16:s['clock_adjustments'].append(sample)
            else:s['clock_adjustments'][-1]=sample
        if reasons:self._set_stop(reasons[0],details=sample,clock=clock,span=span)
        else:s.update(last_wall=clock.wall,last_mono=clock.mono)
        atomic_json(self.path,s)
        if s['stop']:raise EvaluationStopped(s['stop'])

    def check(self):
        with file_lock(str(self.path)+'.lock'):
            self._load();self._check()

    def stop(self,reason):
        with file_lock(str(self.path)+'.lock'):
            self._load()
            if self._set_stop(reason):atomic_json(self.path,self.state)

    def reserve(self,kind):
        if kind not in ('runs','requests'):raise ValueError('Unknown counter')
        with file_lock(str(self.path)+'.lock'):
            self._load();self._check(counters=(kind,))
            self.state[kind]+=1;atomic_json(self.path,self.state);return self.state[kind]

    def begin(self,instance):
        with file_lock(str(self.path)+'.lock'):
            self._load();self._check(counters=('runs','requests'))
            if instance in self.state['instances']:raise EvaluationStopped('Instance already started; read-only recovery only')
            self.state['instances'][instance]='started';atomic_json(self.path,self.state)

    def register_identity(self,instance,sha256):
        with file_lock(str(self.path)+'.lock'):
            self._load()
            if self.state['instances'].get(instance)!='started':raise EvaluationStopped('Identity requires started slot')
            previous=self.state.setdefault('identities',{}).get(instance)
            if previous and previous!=sha256:raise EvaluationStopped('Identity cannot be replaced')
            self.state['identities'][instance]=sha256;atomic_json(self.path,self.state)

    def finish(self,instance):
        # Saved terminal bookkeeping remains permitted after a stop. It cannot
        # reserve work or erase a stop, and read-only recovery never calls it.
        with file_lock(str(self.path)+'.lock'):
            self._load()
            if instance not in self.state['instances']:raise EvaluationStopped('Cannot finish an unstarted instance')
            self.state['instances'][instance]='finished';atomic_json(self.path,self.state)

    def record_provider_cost(self,row):
        if 'max_estimated_cny' not in self.limits:return
        from cost_budget import usage_cost_units
        with file_lock(str(self.path)+'.lock'):
            self._load();s=self.state
            try:
                index=row['index']
                if type(index) is not int or not 1<=index<=s['requests']:raise ValueError('unreserved_request')
                units=usage_cost_units(row)
                ledger=s.setdefault('provider_cost_units',{})
                if str(index) in ledger and ledger[str(index)]!=units:raise ValueError('usage_conflict')
                ledger[str(index)]=units
                s['estimated_cost_units']=sum(ledger.values())
                if s['estimated_cost_units']>=self.limits['max_estimated_cny']*100_000_000:self._set_stop('estimated_cost_budget',details={'provider_index':index})
            except (ValueError,KeyError,TypeError) as exc:
                self._set_stop('provider_cost_unknown',details={'provider_index':row.get('index'),'reason':str(exc)})
                s.setdefault('cost_uncertainties',[]).append({'index':row.get('index'),'reason':str(exc)})
            atomic_json(self.path,s)
