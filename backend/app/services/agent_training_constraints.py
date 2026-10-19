"""Source-bound schedule arithmetic for open-ended training advice.

An envelope is not a medical clearance, an adopted plan, or a semantic score.
It gives the model the same checkable time/weekly limits on every wording,
without replacing other goals or inferring tolerance from prescribed weights.
"""
import json
import math
import re
from langchain_core.messages import ToolMessage
from app.services.agent_trace import observation_fingerprint
from app.services.agent_recovery_bounds import RETURN_LOAD_CONTRACT


def training_schedule(data):
    plan=data.get('plan') if isinstance(data,dict) else None
    if not isinstance(plan,dict) or data.get('found') is not True or plan.get('is_active') is not True:
        return None
    days=plan.get('days_per_week');exercises=plan.get('exercises')
    if type(days) is not int or not 1<=days<=7 or not isinstance(exercises,list) or not 1<=len(exercises)<=40:
        return None
    sessions={};total=0
    for i,ex in enumerate(exercises):
        if not isinstance(ex,dict):return None
        day=ex.get('day_of_week');sets=ex.get('sets');rest=ex.get('rest_seconds')
        reps=re.fullmatch(r'(\d{1,2})(?:[-–~～](\d{1,2}))?',str(ex.get('reps','')))
        if (type(day) is not int or not 1<=day<=7 or type(sets) is not int or not 1<=sets<=10
                or type(rest) is not int or not 30<=rest<=300 or not reps
                or not 1<=int(reps[1])<=30 or reps[2] and not int(reps[1])<=int(reps[2])<=30):return None
        # The name is only display data; selection uses the numeric source row.
        sessions.setdefault(day,[]).append({'source_index':i,'sets':sets,'reps_low':int(reps[1]),
            'reps_high':int(reps[2] or reps[1]),'rest_seconds':rest})
        total+=sets
    if len(sessions)!=days:return None
    return {'basis':'active_plan_prescription_not_actual_training','days_per_week':days,
        'weekly_sets':total,'sessions':[{'day':d,'exercises':sessions[d]} for d in sorted(sessions)],
        'sources':['#/plan/days_per_week',*[f'#/plan/exercises/{i}' for i in range(len(exercises))]],
        'personal_intensity':'unknown','exercise_safety':'not_proven_by_saved_plan'}



def baseline_time_estimate(schedule, minutes):
    """Expose assumptions separately from a prescribed-rest lower bound."""
    rows=[]
    for session in schedule['sessions']:
        exercises=session['exercises']
        low=240+sum(e['sets']*(4*e['reps_low']+e['rest_seconds']) for e in exercises)
        high=240+sum(e['sets']*(4*e['reps_high']+e['rest_seconds']) for e in exercises)
        # Only rests between sets of the same action are unavoidable under
        # this prescription. No rest after the last set or transition guessed.
        rest=sum((e['sets']-1)*e['rest_seconds'] for e in exercises)
        rows.append({'day':session['day'],'estimated_seconds_without_transition':[low,high],
                     'work_seconds_range':[sum(e['sets']*4*e['reps_low'] for e in exercises),sum(e['sets']*4*e['reps_high'] for e in exercises)],
                     'estimated_rest_after_each_set_seconds':sum(e['sets']*e['rest_seconds'] for e in exercises),
                     'preparation_seconds':240,'required_within_exercise_rest_intervals':sum(e['sets']-1 for e in exercises),
                     'within_exercise_rest_lower_bound_seconds':rest,
                     'estimate_relation_to_cap':'unspecified' if minutes is None else 'over' if low>minutes*60 else 'within' if high<=minutes*60 else 'straddles',
                     'rest_lower_bound_exceeds_cap':None if minutes is None else rest>minutes*60})
    return {'basis':'saved_plan_with_assumed_pace_not_measured','assumed_seconds_per_repetition':4,
            'warmup_seconds':180,'finish_seconds':60,'count_rest_after_each_set':True,
            'transition_seconds':None,'actual_tempo_measured':False,'is_duration_lower_bound':False,
            'rest_bound_requires_prescribed_rest_intervals':True,'sessions':rows}


def _draft_work_states(text,names):
    """Carry one explicit temporary recipe, including consecutive action bullets."""
    if len(set(names))!=len(names):return
    quoted=re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
    temporary=False;current=None;group=None;group_end=None;bullet_chain=False
    for line in re.finditer(r'[^\n]+',text):
        raw=quoted.sub(lambda m:' '*len(m.group()),line.group());heading=raw.replace('*','').strip()
        if re.search(r'(?:临时|本周|这周|这一周).{0,30}(?:安排|建议|方案)|(?:省时)?减量(?:安排|建议|方案)',heading[:65]):
            temporary=True;current=None;group=None;bullet_chain=False
        elif re.match(r'\s*(?:\*\*|#{1,6}\s)',raw):temporary=False;current=None;group=None;bullet_chain=False
        if not temporary:continue
        bullet=bool(re.match(r'\s*[-*]\s+',raw));described=False
        for i,sentence in enumerate(re.finditer(r'[^。！？；]+',raw)):
            value=sentence.group();start=line.start()+sentence.start();end=line.start()+sentence.end()
            counts=list(re.finditer(r'\d+\s*组',value))
            changed=bool(re.search(r'(?:改|减|增|调整)(?:为|到|至)',value))
            describes=bool(counts or changed or bullet and any(n in value for n in names))
            if describes:
                continuing=bullet and bullet_chain and i==0 and not changed
                if not continuing:current=[];group=start
                group_end=end;described=True;parsed=None
                if not re.search(r'原计划|已存计划|已保存|假设|如果|若|错误示例|反例|朋友|别人|同事|不安排|不要|不做|暂停|取消|不能|周[一二三四五六日天]|第[一二三四五六七\d]天',value):
                    pairs=[(n,m) for n in names for m in re.finditer(re.escape(n)+r'\s*(?P<sets>[1-9]\d*)\s*组',value)]
                    reps=re.findall(r'每组\s*(\d+)\s*次',value);rests=re.findall(r'组间休息\s*(\d+)\s*秒',value)
                    inline=[(n,m) for n in names for m in re.finditer(re.escape(n)+r'\s*(?P<sets>[1-9]\d*)\s*组\s*[×xX*]\s*(?P<reps>\d+)\s*次',value)]
                    if pairs and len({n for n,_ in pairs})==len(pairs)==len(counts) and all(1<=int(m['sets'])<=10 for _,m in pairs):
                        values={n:int(m['reps']) for n,m in inline} if len(inline)==len(pairs) and not reps else {n:int(reps[0]) for n,_ in pairs} if not inline and len(reps)==1 else {}
                        if len(values)==len(pairs) and all(1<=r<=30 for r in values.values()):
                            rest=int(rests[0]) if len(rests)==1 and 30<=int(rests[0])<=300 else None
                            parsed=[dict(exercise_name=n,source_index=names.index(n),sets=int(m['sets']),reps=values[n],rest_seconds=rest,start=start,end=end,quote=text[start:end]) for n,m in pairs]
                if parsed is None or current is None or {r['exercise_name'] for r in current}&{r['exercise_name'] for r in parsed}:current=None
                else:current=current+parsed
            yield dict(offset=start,end=end,group_start=group,group_end=group_end,exercises=current,value=value)
        bullet_chain=bullet and described


def draft_work_time_basis(text,records):
    """Arithmetic on draft operands, never a prescription or a measured duration."""
    import hashlib
    plans=[(cid,d) for tool,cid,d in records if tool=='plan.get_active' and d.get('executed') is not False]
    if not plans:return []
    cid,data=plans[-1];schedule=training_schedule(data)
    if not schedule or len(schedule['sessions'])!=1:return []
    names=[e.get('exercise_name') for e in data['plan']['exercises']]
    if any(not isinstance(n,str) or not n for n in names):return []
    groups={};paces={};uncertain=set()
    for state in _draft_work_states(text,names):
        key=state['group_start']
        if key is None:continue
        groups[key]=state
        value=state['value']
        if re.search(r'秒\s*/\s*次|每次(?:动作)?[^，,。！？；\n]{0,12}秒',value):
            matches=list(re.finditer(r'每次(?:动作)?(?:约)?\s*(\d+)\s*秒|(?<![\d.\-–~～])(?P<per_rep>\d+)\s*秒\s*/\s*次',value))
            if len(matches)!=1 or not 1<=int(matches[0][1] or matches[0]['per_rep'])<=30:uncertain.add(key)
            else:paces.setdefault(key,set()).add(int(matches[0][1] or matches[0]['per_rep']))
    found=[]
    for key,state in groups.items():
        if not state['exercises'] or key in uncertain or len(paces.get(key,set()))>1:continue
        pace=next(iter(paces[key])) if paces.get(key) else 4
        reps=sum(e['sets']*e['reps'] for e in state['exercises'])
        found.append(dict(draft_sha256=hashlib.sha256(text.encode()).hexdigest(),plan_source_call_id=cid,
            group_start=key,group_end=state['group_end'],exercises=state['exercises'],total_repetitions=reps,
            assumed_seconds_per_repetition=pace,work_seconds=reps*pace,
            pace_source='draft_explicit_assumption' if paces.get(key) else 'envelope_assumption_not_measured',
            excludes_rest_preparation_and_transition=True,not_a_prescription_or_safety_clearance=True))
    return found


def _time_arithmetic_findings(text, records, schedule, estimate):
    """Check only complete, explicitly scoped timing operands, not prose in general."""
    if len(schedule['sessions'])!=1:return []
    quoted=re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
    duration=r'(?P<minutes>\d+(?:\.\d+)?)\s*(?:分钟|分)(?:(?P<seconds>\d+)\s*秒)?'
    rest_claim=re.compile(r'(?:必要的?|同动作内必要的?)?组间休息(?:本身)?(?:就有|至少(?:需要)?|合计|总计|共|为|是)?\s*'
                          r'(?:(?P<count>\d+)\s*[×xX*]\s*(?P<rest>\d+)\s*秒\s*[=＝]\s*)?'+duration)
    found=[];session=schedule['sessions'][0];expected=estimate['sessions'][0]
    rests={e['rest_seconds'] for e in session['exercises']}
    def add(a,b):found.append(dict(code='training_action_conflict',quote=text[a:b],start=a,end=b))
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=quoted.sub(lambda m:' '*len(m.group()),sentence.group())
        paragraph=text[text.rfind('\n',0,sentence.start())+1:sentence.end()]
        if not re.search(r'原计划|已存计划|原处方|生效计划',paragraph) or re.search(r'错误示例|反例|朋友|别人',raw):continue
        for m in rest_claim.finditer(raw):
            if not re.search(r'必要|本身|至少',m.group()):continue
            if re.search(r'不能(?:说|断言)|并非|不是|是否|不确定|未知',raw[:m.start()]):continue
            if re.match(r'\s*(?:吗|么|[?？])',raw[m.end():]) or text[sentence.end():].startswith('？'):continue
            seconds=float(m['minutes'])*60+int(m['seconds'] or 0)
            bound=expected['within_exercise_rest_lower_bound_seconds']
            mismatch=seconds>bound if '至少' in m.group() else seconds!=bound
            if m['count'] and len(rests)==1:
                mismatch=mismatch or int(m['count'])!=expected['required_within_exercise_rest_intervals'] or int(m['rest'])!=next(iter(rests))
            if mismatch:add(sentence.start()+m.start(),sentence.start()+m.end())
    data=[d for tool,_,d in records if tool=='plan.get_active' and d.get('executed') is not False][-1]
    names=[e.get('exercise_name') for e in data['plan']['exercises']]
    names=[n for n in names if isinstance(n,str) and n]
    states={row['offset']:row for row in _draft_work_states(text,names)}
    for line in re.finditer(r'[^\n]+',text):
        raw=quoted.sub(lambda m:' '*len(m.group()),line.group())
        for sentence in re.finditer(r'[^。！？；]+',raw):
            value=sentence.group();state=states.get(line.start()+sentence.start())
            if not state or not state['exercises']:continue
            rows=state['exercises'];work_reps=sum(e['sets']*e['reps'] for e in rows)
            reps={e['reps'] for e in rows};rests={e['rest_seconds'] for e in rows}
            recipe=(sum(e['sets'] for e in rows),next(iter(reps)),next(iter(rests))) if len(reps)==len(rests)==1 and None not in rests else None
            tempo_matches=list(re.finditer(r'每次(?:动作)?(?:约)?\s*(\d+)\s*秒|(?<![\d.\-–~～])(?P<per_rep>\d+)\s*秒\s*/\s*次',value))
            pace=[m[1] or m['per_rep'] for m in tempo_matches]
            if len(pace)!=1 or not 1<=int(pace[0])<=30:continue
            if re.search(r'错误示例|反例|朋友|别人',value):continue
            # Work alone uses the actual chosen repetitions, independent of
            # rests and preparation. Display rounding is not a 60-second waiver.
            if work_reps is not None:
                pattern=r'动作(?:时间|时长)?(?P<qualifier>(?:约|大约|共|合计|为|是|需要|用时|耗时|\s){0,8})(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>分钟|秒)'
                for claim in re.finditer(pattern,value):
                    # A per-repetition duration is an operand, not the sum.
                    if any(claim.start()<m.end() and m.start()<claim.end() for m in tempo_matches):continue
                    lead=re.split(r'[，,]|但是|不过|然而|但',value[:claim.start()])[-1]
                    after=value[claim.end():]
                    # A named exercise or per-set/rep subtotal is not the total
                    # work component. This check does not certify those subtotals.
                    if re.search(r'(?:每组|每次|每个|单组|单次|单个|一组)(?:的)?\s*$',lead):continue
                    if any(re.search(re.escape(n)+r'(?:的)?[：:]?\s*$',lead) for n in names):continue
                    if re.search(r'是否|会不会|不确定|不能(?:说|断言)|并非|不是',lead):continue
                    if re.match(r'\s*(?:吗|么|[?？])',after) or raw[sentence.end():].startswith('？'):continue
                    factor=60 if claim['unit']=='分钟' else 1
                    digits=len(claim['number'].split('.')[1]) if '.' in claim['number'] else 0
                    tolerance=factor*0.5*10**(-digits) if '约' in claim['qualifier'] else 0
                    if abs(float(claim['number'])*factor-work_reps*int(pace[0]))>tolerance+1e-9:
                        a=line.start()+sentence.start()+claim.start();add(a,line.start()+sentence.start()+claim.end())
            if recipe is None:continue
            rests_after=re.findall(r'每组后(?:休息)?\s*(\d+)\s*秒',value)
            if len(rests_after)!=1 or int(rests_after[0])!=recipe[2]:continue
            pattern=r'(?:这套|该方案|此方案|动作和休息|动作与休息)(?:已经|仅|约|大约|接近|估算|预计|需要|共|合计|耗时|用时|为|是|\s){0,12}'+duration
            for m in re.finditer(pattern,value):
                # The following addition explicitly says preparation is not
                # yet in this subtotal. No guess for a vague total duration.
                if not re.match(r'\s*[，,]\s*(?:再)?加上热身(?:和)?收尾',value[m.end():]):continue
                if re.search(r'是否|会不会|不确定|不能(?:说|断言)|并非|不是',value[:m.start()]):continue
                seconds=float(m['minutes'])*60+int(m['seconds'] or 0)
                subtotal=recipe[0]*(recipe[1]*int(pace[0])+recipe[2])
                if abs(seconds-subtotal)>60:
                    start=line.start()+sentence.start()+m.start();add(start,line.start()+sentence.start()+m.end())
    return found



def _session_cap_override_findings(text, query):
    """A sourced session ceiling cannot be waived by the answer itself.

    This finite check does not infer a ceiling from measured past durations,
    nor treat shorter sessions as overriding it. Ambiguous inputs abstain.
    """
    from app.services.agent_assertion_scope import nonassertive
    quoted=re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
    plain=quoted.sub(lambda m:' '*len(m.group()),query)
    cap_pattern=re.compile(r'(?:(?:每次|单次)(?:训练|锻炼)?(?:最多|不超过|不多于|至多)?|(?:每次)?上限(?:是|为)?)\s*(\d+)\s*分钟')
    limits=set()
    for clause in re.finditer(r'[^。！？；\n]+',plain):
        raw=clause.group()
        if re.search(r'如果|假如|假设|若|以前|过去|朋友|同事|别人|(?:他|她)的',raw):continue
        # Explicitly withdrawn limits are not still-current instructions.
        cancel=re.search(r'(?:取消|撤销|解除)(?:了)?(?:每次)?(?:\d+\s*分钟(?:的)?)?(?:上限|时限|限制)|(?:上限|时限|限制)(?:已经|已|现已)?(?:取消|撤销|解除)|不设(?:上限|时限|限制)',raw)
        if cancel and not re.search(r'不要|不能|没有|尚未|不应',raw[:cancel.start()]):return []
        for cap in cap_pattern.finditer(raw):
            lead=re.split(r'[，,]|但是|不过|然而|但',raw[:cap.start()])[-1]
            lead=re.sub(r'(?:不要|不能|不应)(?:取消|撤销|解除)\s*','',lead)
            if re.search(r'是否|能否|不知道|不确定|不(?:要|想|要求|接受)|不是|并非',lead):continue
            if re.match(r'\s*(?:吗|么|[?？])',raw[cap.end():]) or plain[clause.end():].startswith('？'):continue
            limits.add(int(cap[1]))
    if len(limits)!=1:return []
    minutes=next(iter(limits))
    if not 1<=minutes<=120:return []
    value=rf'{minutes}\s*分钟'
    override=re.compile(r'(?:时间|时长)\s*不限(?:于|制在|定在)?\s*'+value+
                        r'|(?:不受|不用受|无需受)\s*'+value+r'(?:的)?(?:上限|时限|限制)(?:约束)?'+
                        r'|(?:可以|允许|可)(?:适当)?超过\s*'+value)
    found=[]
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=quoted.sub(lambda m:' '*len(m.group()),sentence.group())
        following=text[sentence.end():]
        for claim in override.finditer(raw):
            prefix=raw[:claim.start()];after=raw[claim.end():]
            scope=re.split(r'但是|不过|然而|可是|却|但',prefix)[-1]
            local=re.split(r'[，,]',scope)[-1]
            if re.search(r'如果|假如|假设|若|以前|过去|朋友|同事|别人|(?:他|她)的|错误示例|反例',scope):continue
            if re.search(r'休息|睡觉|通勤|工作|就医|旧计划|原计划|已存计划',local):continue
            if re.search(r'不是说|并非',local) or nonassertive(raw,claim.start(),claim.end(),'training_action_conflict',following):continue
            if re.match(r'\s*(?:吗|么|[?？]|(?:这个)?说法(?:不成立|没有依据)|是否成立)',after):continue
            if not after.strip() and following.startswith(('？','?')):continue
            a=sentence.start()+claim.start();b=sentence.start()+claim.end()
            found.append({'code':'training_action_conflict','quote':text[a:b],'start':a,'end':b})
    return found


def training_time_findings(text, records, query):
    """Locate explicit certainty about a sourced plan's time infeasibility.

    Passing this finite check certifies neither timings nor exercise safety.
    A qualified estimate is distinct from an unconditional impossibility.
    """
    quoted=re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
    caps={int(n) for n in re.findall(r'(\d+)\s*分钟',quoted.sub('',query))}
    if len(caps)!=1 or not 1<=next(iter(caps))<=120:return []
    plans=[training_schedule(data) for tool,_,data in records if tool=='plan.get_active' and data.get('executed') is not False]
    if not plans or plans[-1] is None:return []
    estimate=baseline_time_estimate(plans[-1],next(iter(caps)))
    # Do not combine a week's rests into one session or infer every day's
    # infeasibility from a single long day.
    arithmetic=_time_arithmetic_findings(text,records,plans[-1],estimate)+_session_cap_override_findings(text,query)
    if all(r['rest_lower_bound_exceeds_cap'] for r in estimate['sessions']):return arithmetic
    subject=r'(?:原样(?:照做)?|原计划|已存计划|原方案|原处方|原来的计划|全部组数|\d+\s*(?:个)?工作组)'
    impossible=r'(?:塞不进(?:去)?|塞不下|装不进(?:去)?|装不下|放不下|做不完|无法完成|不能完成|不可能完成|远超\s*\d+\s*分钟|(?:一定|肯定|必然)(?:超时|超过\s*\d+\s*分钟))'
    patterns=[re.compile(subject+r'[^。！？；\n]{0,28}?(?P<claim>'+impossible+')'),
              re.compile(r'(?:\d+\s*分钟(?:内)?|这个上限|该时限)[^。！？；\n]{0,12}?(?P<claim>'+impossible+')[^。！？；\n]{0,15}?'+subject)]
    # Resolve only a near reference to the unchanged source prescription.
    # A new recipe, another subject, or a paragraph boundary breaks the link.
    definite=r'(?:本来(?:也|就)?|原本(?:也|就)?|本就)'
    hard_claim=definite+r'(?:'+impossible+r'|(?:需要|必须)(?:压缩|减量))'
    near_pattern=re.compile(r'^\s*(?:按|照)(?:这个|这些|上述)组数[^。！？；\n]{0,70}?(?P<claim>'+hard_claim+'|'+impossible+')')
    changed=re.compile(r'改(?:成|为|到)|调整(?:为|到)|减(?:为|到|至)|增至|临时(?:方案|安排)|新方案')
    # A long source description may precede an explicitly definite conclusion.
    # Extend only that construction, not every uncertain or unrelated predicate.
    definite_pattern=re.compile(subject+r'[^。！？；\n]{0,160}?(?P<claim>'+hard_claim+')')
    consequence=re.compile(r'^\s*(?:所以|因此|因而|也就是说)[^。！？；\n]{0,60}?(?:原计划|原处方|原方案)(?:本身)?(?:就|也)?(?P<claim>不适配|不可行)')
    # A same-paragraph causal conclusion has its own certainty scope. Its
    # premise must still describe the unchanged sourced plan, not a new recipe.
    time_reduction=r'时间上(?:也|就)?(?:确实|必然|肯定)(?:需要|必须|要)(?:压缩|减量|砍)'
    causal_claim=r'(?:'+impossible+'|'+hard_claim+'|'+time_reduction+')'
    causal_source=re.compile(subject+r'[^。！？；\n]{0,160}?(?:所以|因此|因而|也就是说)[^。！？；\n]{0,60}?(?P<claim>'+causal_claim+')')
    causal_follow=re.compile(r'^\s*(?:所以|因此|因而|也就是说)[^。！？；\n]{0,60}?(?P<claim>'+causal_claim+')')
    over_time=re.compile(r'超时|超(?:过)?[^，,。！？；\n]{0,20}分钟')
    time_target=r'\s*(?:(?:你(?:的)?|这个|该)?(?:\d+\s*分钟(?:内)?(?:的)?(?:时限|上限)?|时限|上限|时间))?\s*'
    found={(f['start'],f['end']):f for f in arithmetic};previous=None;prior_time_error=False
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=quoted.sub(lambda m:' '*len(m.group()),sentence.group())
        antecedent=quoted.sub(lambda m:' '*len(m.group()),previous.group()) if previous and '\n' not in text[previous.end():sentence.start()] else ''
        follows_time_error=bool(antecedent) and prior_time_error
        previous=sentence;prior_time_error=False
        # Revise an immediate causal suitability conclusion together with the
        # unsupported time claim. Independent symptom reasons remain separate.
        follow=consequence.search(raw) if follows_time_error and not re.search(r'朋友|同事|别人|(?:他|她)的|错误示例|反例',raw) else None
        if follow and not changed.search(raw) and not re.search(r'因[^，,]{0,12}(?:疼|痛|症状)',raw):
            local=re.split(r'[，,]',raw[:follow.start('claim')])[-1]
            after=raw[follow.end():]
            if (not re.search(r'不能(?:说|断言)|无法(?:确定|确认)|是否|未必|可能|不一定|并非|不是',local)
                    and not re.match(r'\s*(?:吗|么|[?？])',after)
                    and not (text[sentence.end():].startswith('？') and not after.strip())):
                a=sentence.start()+follow.start();b=sentence.start()+follow.end()
                found[(a,b)]={'code':'training_action_conflict','quote':text[a:b],'start':a,'end':b}
        near_plan=bool(re.search(r'原(?:来的)?计划|已存计划|原方案|原处方',antecedent) and not changed.search(antecedent)
                       and not re.search(r'朋友|同事|别人|(?:他|她)的|错误示例|反例',antecedent))
        causal_antecedent=bool(near_plan and over_time.search(antecedent)
                               and not re.search(r'不能(?:说|断言)|并非|不是|未超过|没(?:有)?超过|是否',antecedent))
        if (not re.search(r'分钟|时间|上限|时限',raw) and not causal_antecedent) or re.search(r'错误示例|反例|朋友|同事|别人',raw):continue
        for pattern in patterns+[definite_pattern,causal_source]+([near_pattern] if near_plan else [])+([causal_follow] if causal_antecedent else []):
            for m in pattern.finditer(raw):
                lead=re.split(r'所以|因此|因而|也就是说',raw[:m.start('claim')])[-1];after=raw[m.end():]
                if '远超' in m.group('claim'):
                    # Only compare this explicit conclusion with the user's
                    # sourced cap; unrelated numeric claims remain unverified.
                    if int(re.search(r'远超\s*(\d+)',m.group('claim'))[1])!=next(iter(caps)):continue
                    lead=re.split(r'但是|不过|然而|但',lead)[-1]
                if pattern is near_pattern:
                    if changed.search(raw[:m.start('claim')]):continue
                    if re.search(r'因[^，,]{0,12}(?:疼|痛|症状)[^，,]{0,12}$',lead):continue
                if changed.search(raw[m.start():m.start('claim')]):continue
                # A denial before a causal connector still scopes the full
                # conclusion, including matches from the shorter source rule.
                causal_lead=re.split(r'但是|不过|然而|但',raw[:m.start('claim')])[-1]
                if re.search(r'不能(?:说|断言|确定|确认)|无法(?:确定|确认)|没有依据(?:说|断言)|不足以(?:说|断言)|并非|不是',causal_lead):continue
                if any(word in m.group('claim') for word in ('压缩','减量','砍')):
                    # Reduction advice is not itself a time-impossibility claim.
                    # Require an explicit over-time premise in this source scope;
                    # a later symptom/fatigue reason is a separate basis to reduce.
                    scope=(antecedent+'。' if pattern is causal_follow else '')+raw[m.start():m.start('claim')]
                    over=list(over_time.finditer(scope))
                    if not over:continue
                    reason=scope[over[-1].end():]
                    if not re.match(time_reduction,m.group('claim')) and re.search(r'疼|痛|症状|疲劳|累|睡眠|没睡|缺觉',reason):continue
                    denial=re.split(r'但是|不过|然而|但',raw[:m.start('claim')])[-1]
                    if re.search(r'不能(?:说|断言|确定|确认)|无法(?:确定|确认)|没有依据(?:说|断言)|不足以(?:说|断言)|并非|不是',denial):continue
                if m.group().startswith('原样') and not re.search(r'原计划|已存计划|原方案|原处方',raw):continue
                if re.search(r'是否|不确定|不能(?:说|断言|确定|确认)|无法(?:确定|确认)|没有依据(?:说|断言)|不足以(?:说|断言)|并非|不是',lead):continue
                hard=bool(re.match(definite,m.group('claim')) or re.search(r'[，,]\s*'+definite+r'$',lead))
                estimate_scope=re.split(r'[，,]',lead)[-1] if hard else lead
                if re.search(r'估算|估计|预计|大约|约(?:需|为|\d)|可能|未必|不一定',estimate_scope):continue
                question_tail=re.sub(r'^\s*(?:原组数|全部组数|原(?:来的)?计划|原处方)\s*','',after)
                if re.match(r'\s*(?:吗|么)',question_tail):continue
                # The queried object may follow the predicate: 装不下原组数吗.
                if re.fullmatch(time_target+r'(?:(?:吗|么)[?？]?|[?？])',question_tail):continue
                if text[sentence.end():].startswith('？') and re.fullmatch(time_target,question_tail):continue
                a=sentence.start()+m.start();b=sentence.start()+m.end()
                found[(a,b)]={'code':'training_action_conflict','quote':text[a:b],'start':a,'end':b}
                prior_time_error=True
    return [found[key] for key in sorted(found)]


def adjustment_budget(schedule,minutes):
    if not schedule or minutes is not None and (type(minutes) is not int or not 1<=minutes<=120):return None
    base=schedule['weekly_sets'];base_days=schedule['days_per_week'];sessions=[]
    for original in schedule['sessions'][:min(base_days,2)]:
        remaining=minutes*60-240 if minutes is not None else math.inf;chosen=[]
        for ex in original['exercises']:
            chosen.append({**ex,'sets':0,'original_sets':ex['sets'],'reps':min(ex['reps_low'],8)})
        for step in range(max(math.ceil(ex['original_sets']/2) for ex in chosen)):
            for ex in chosen:
                cost=ex['reps']*4+ex['rest_seconds']
                if step<math.ceil(ex['original_sets']/2) and remaining>=cost:
                    ex['sets']+=1;remaining-=cost
        sessions.append({'original_day':original['day'],'exercises':chosen})
    # A one-set original prescription cannot be reduced by rounding halves up.
    # Remove a working set explicitly instead of adding frequency or workload.
    total=sum(ex['sets'] for s in sessions for ex in s['exercises'])
    for s in reversed(sessions):
        for ex in reversed(s['exercises']):
            if total>=base and ex['sets']:
                ex['sets']-=1;total-=1
    for s in sessions:
        s['exercises']=[ex for ex in s['exercises'] if ex['sets']]
        s['estimated_seconds']=240+sum(ex['sets']*(ex['reps']*4+ex['rest_seconds']) for ex in s['exercises'])
        s['timing_components']={'work_seconds':sum(e['sets']*e['reps']*4 for e in s['exercises']),
                                'estimated_rest_after_each_set_seconds':sum(e['sets']*e['rest_seconds'] for e in s['exercises']),
                                'preparation_seconds':240,
                                'within_exercise_rest_lower_bound_seconds':sum((e['sets']-1)*e['rest_seconds'] for e in s['exercises'])}
    sessions=[s for s in sessions if s['exercises']]
    allocation={'baseline_days':base_days,'baseline_weekly_sets':base,'temporary_days':len(sessions),
        'temporary_weekly_sets':total,'session_cap_minutes':minutes,'sessions':sessions,
        'allocation_status':'conditional_reduced_schedule' if total else 'no_work_sets_fit',
        'count_rest_after_each_set':True,'warmup_seconds':180,'finish_seconds':60,
        'assumed_seconds_per_repetition':4,'timing_is_estimate':True,
        'exercise_selection_is_not_medically_validated':True,'source_paths':schedule['sources']}
    from app.services.agent_recovery_bounds import recovery_bounds
    allocation['return_progression']=recovery_bounds(schedule,allocation)
    allocation['baseline_time_estimate']=baseline_time_estimate(schedule,minutes)
    return allocation


CONTRACT='''
临时训练约束：training_adjustment_envelope来自本轮实际工具结果和当前用户原文，不是已经保存的计划，也不是医学适配证明。
先区分原生效计划每周天数/组量与档案偏好、实际训练量。疲劳减量不能增周工作组数或天数；不要把每次组数少误当作每周减量。
普通疲劳与新发/复发症状必须分开：如果疼痛、酸胀或不适出现，先停止诱发动作并重新确认，不把原力量动作减组、减重作为继续执行的方案。不能把“更累或疼痛”合并成同一个减量分支；已恢复史仍按用户当前无痛事实处理，改善后的回归另列。
session_cap_minutes包含热身、动作、切换、所有休息和收尾。示例按每次动作4秒估算并保守计每组后的休息，实际需要更多时间时省略组/动作或停止，不能缩休息赶动作。上限不是必须填满。“不必凑满”不等于取消时限：当前训练及替代轻活动仍遵守用户的单次上限，不写“时间不限N分钟/可以超过N分钟”后又说N分钟是上限。用户明确撤销或更改限制才调整，休息与其他日常活动时长另行区分。timing_components把动作、估算休息、热身收尾分列；estimated_seconds已包含准备收尾，不能再加一次。每组后休息的保守估算包含最后一组之后，不等于同动作内必要组间休息；后者每个动作只有组数减一段，不能把6组直接当6段必要间隔。若答复改用了另一组次方案，须按实际组数×次数×明确秒数重新计算动作时长，再加所声明休息和一次准备收尾；不能套用原数值示例的总时长。
原计划时间结论也须有依据：baseline_time_estimate逐日列出按4秒/次、每组后休息及热身收尾4分钟的估算，换场耗时和实际节奏未知；估算不是实测下界。应说“按这些假设预计超时”，不能断言“原组数必然塞不进上限”或“光这两个动作就远超时限”，也不能由“超过12分钟还需收尾”推出一定超过15分钟。只有处方内必要组间休息下界已严格超过上限时，才可在保留该休息处方的前提下判不可行。不要混加多日训练；因症状暂停的理由独立说明，不编造时间理由，也不承诺能按时练完。
session_cap_minutes为null时，用户没有给出每次硬时限。仍遵守生效计划周量与频率边界；estimated_seconds只是该减量方案的估算用时，不得改称用户要求的时限，也不能用“没给分钟数”跳过减量。
训练效果与方案覆盖范围分开：仅凭分钟数或工作组数，不能断言“没有训练刺激”“达不到刺激”“白练”或“效果为零”，也不能反向保证少练必然有效或效果不变。条件不足时说明不能保证覆盖原计划的全部目标，以实际时限、诱因和恢复信息解释取舍；不拿没有依据的生理效果结论补强暂休理由。
若allocation_status为no_work_sets_fit，明确暂时不安排原力量组，说明原训练目标本次无法完整覆盖；在时限内只选不诱发症状的轻活动或休息，不另加一套动作保证练到位。若能容纳部分组，说明明确取舍，保留其余目标。
sessions仅是原动作的数值可行示例。当前疼痛、旧伤和未确认适配仍优先，不能照抄有症状动作、换成更多组的替代动作，不能把上肢/下肢/核心分类当无负荷证明。不得承诺卧推、单车、平板等不涉及另一部位；姿势/支撑未知就是未知。
当前动作做法或适配待确认时，“如果做的时候不疼，就可以少量试/先试练”仍是先试再判断，不能代替执行前核实；没有数字组数也属于当前训练指令。不要用假设执行中无痛充当症状已恢复或做法已确认的事实。当前取舍与恢复后试练分开：确认实际做法及症状/日常功能恢复等前提后，再按耐受逐步回归。
当前症状必要信息已给则使用；只问实际缺少且影响选择的部分。停止诱发动作可作为阶段行动，但不能用一律拒绝替代用户已可完成的子目标。
回归不得先增天数/周组量再降回原计划。以症状、日常功能、训练后和次日恢复为条件，逐次小幅恢复、一次改一项、不过原处方上限；保存公斤数不是安全目标，不定第几次必须回到某重量，不保证休息或替代训练不影响目标。
return_progression提供这份临时分配的逐动作组数边界和有条件数值步骤。每一步只给一个动作加一组；从总3组直接恢复6组不属于一步逐渐回归。先稳定组数，再恢复次数，最后按实际恢复小幅试负重；“稳定一两次后恢复原重量”仍把次数当成依据，不能这样写。当前生效计划的天数是上限，档案偏好不能作为本次回归目标。
回归次数必须从答复实际采用的临时处方开始：return_reps等数值只属于这份分配示例，不能把示例的8次套到实际选择10次的方案。若临时方案已保持原处方次数，回归时保持并观察耐受，不虚构从更低次数恢复的步骤；明确中间调整后，后一步从调整后的次数开始。组数、每组次数和每周频率分别核对，保留条件与时限。
count_steps仅适用于采用该临时分配且身体条件允许的情况，不能照搬到另一种删动作/改组数的方案；另一方案需以各动作实际保留组数为起点重新逐步计算。remaining_source_indices表示仍不能在当前时限/频率下恢复的动作，必须重新核对限制，不跳到原计划。暂停动作和恢复频率也必须重新确认症状及功能；没有负重数字不代表没有身体负荷。
用户的期限、动作偏好、其他查询目标继续有效；如与数值边界或症状冲突，解释取舍并询问必要选择，不能偷偷覆盖。以下是建议数值边界，不是对所有答案的自动安全认证。
'''


def training_adjustment_context(query,resolution,messages,task_state=None):
    if (not isinstance(query,str) or not resolution or resolution.request_kind not in {'query','assessment'}
            or resolution.requested_effect!='read' or resolution.risk_level=='high'
            or resolution.change_requests or resolution.clarification_required):return ''
    # Read current-user limits only. Do not silently take a model paraphrase's
    # invented number or a previous assistant's prescription as authorization.
    plain=re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"','',query)
    from app.schemas.agent_task import TaskSnapshot
    from app.services.agent_task_state import active_task
    task=(active_task(task_state) if isinstance(task_state,TaskSnapshot)
          and task_state.transition in {'continue','resume'} else None)
    inherited=[r for r in task.requirements if r.kind=='goal'] if task else []
    request_source=plain+'；'+'；'.join(r.quote for r in inherited)
    if not re.search(r'临时|过渡|减量|保底|回归|工作.*累|工作.*疲',request_source):return ''
    caps=set(int(n) for n in re.findall(r'(?:每次|单次)?(?:最多|不超过|上限|不多于)?\s*(\d+)\s*分钟',plain))
    limit_source='current_user_message';limit_quotes=[]
    if not caps and inherited:
        limit_quotes=[{'quote':r.quote,'source_run_id':r.source_run_id} for r in inherited if re.search(r'\d+\s*分钟',r.quote)]
        caps={int(n) for r in limit_quotes for n in re.findall(r'(\d+)\s*分钟',r['quote'])}
        limit_source='validated_active_task_requirements'
    if len(caps)>1:return ''
    if not caps:limit_source='unspecified_by_user'
    plan_messages=[];health=False
    for m in messages:
        if (not isinstance(m,ToolMessage) or m.status=='error'
                or m.additional_kwargs.get('executed') is False):continue
        try:data=json.loads(m.content)
        except (TypeError,ValueError):continue
        if (not isinstance(data,dict) or data.get('error') or data.get('error_code')
                or data.get('executed') is False or data.get('found') is not True):continue
        if m.name=='health_get_screening_summary':health=True
        if m.name=='plan_get_active':plan_messages.append(m)
    if not health or not plan_messages:return ''
    m=plan_messages[-1]
    try:spec=adjustment_budget(training_schedule(json.loads(m.content)),next(iter(caps),None))
    except (TypeError,ValueError):return ''
    if spec is None:return ''
    from app.services.agent_variant_grounding import movement_source_bindings,CAUSAL_SOURCE_CONTRACT
    bindings=movement_source_bindings([('plan.get_active',m.tool_call_id,json.loads(m.content))],query)
    return '\n'+CONTRACT+CAUSAL_SOURCE_CONTRACT+RETURN_LOAD_CONTRACT+'\n'+json.dumps({'training_adjustment_envelope':spec,
        'movement_source_bindings':bindings,
        'plan_source_call_id':m.tool_call_id,'plan_result_sha256':observation_fingerprint(m.content),
        'limit_source':limit_source,'limit_quotes':limit_quotes,'symptom_and_other_goals_must_remain':True},ensure_ascii=False)
