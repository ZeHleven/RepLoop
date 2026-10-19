"""Detect bounded mechanism claims unsupported by the observed exercise variant.

A plan label and prescribed kilograms do not identify hand placement. This
check neither certifies other exercise facts nor decides personal suitability.
"""
import re
from app.services.agent_assertion_scope import BODY, PERSONAL_LOAD_CONTRACT, nonassertive


CAUSAL_SOURCE_CONTRACT='''
动作理由来源约束：movement_source_bindings中的名称和公斤数不证明手腕承重、手部支撑/握持或运动方向；共同指代“两个动作/它们各自”也要逐个有依据。已确认变式只支持对应的机制，不自动证明个人耐受。原动作名称和替换对象也必须有来源：用户只说卧推时，不能写成“把杠铃卧推换掉了”或“你原来的哑铃卧推”，再在后面说器械未知；一般动作解释、待选的新变式与用户原动作分开，替换目标不证明被替换动作的器械。明确来源只支持匹配变式，不由哑铃或上斜推成杠铃，也不由杠铃补出平板。
起因不等于诱发动作方向：用户说提包后开始疼、抬手过头才疼，不能改写成提包需要过头；泛称卧推也不能断言与过头疼方向重叠。未知做法不能说已经撞上诱因，暂停可依据现有症状与尚未核实的适配解释，不编造因果理由。
已恢复用户仍按当前无痛与功能正常的原文处理，不因动作来源未知就改判当前疼痛或一律暂停。区分已知、未知、条件建议，只询问会影响本次选择且未提供的信息。
'''+PERSONAL_LOAD_CONTRACT

_QUOTED=re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
_VARIANT=r'(?:高脚杯(?:式|深蹲)?|(?:手持)?哑铃深蹲|杠铃(?:背|前)蹲|背杠深蹲|徒手深蹲)'
_MECHANISM=re.compile(r'手部(?:参与|支撑|持重)|手持(?:/背负)?(?:杠铃|哑铃|负重|重量)?|(?:手部|用手|双手)?(?:抓握|握持)|握重物|撑地|用手拿|用到(?:左|右|双)?手腕|(?:抬手|手臂|举手)?过头')
_UNCERTAIN=re.compile(r'是否|会不会|能否|不一定|未必|可能|不代表|不等于|未知|不清楚|没(?:有)?(?:说|说明)|未(?:说明|确认|指定)|需(?:要)?(?:确认|核实)|请(?:说明|确认|补充)|怎么|如何|哪种|可(?:以)?采用|建议改(?:成|为)|不能(?:据此|直接|就|一概)?(?:确定|确认|断言|推断|认为|认定|说)|无法(?:确定|确认|断言)|没有依据|(?:没(?:有)?|未)(?:记录|记载)(?:清楚|具体)?\s*$')


def _confirmed_squat(text,*,stored=False):
    if not isinstance(text,str):return []
    quotes=[]
    for clause in re.split(r'[。！；\n]',_QUOTED.sub('',text)):
        if re.search(r'如果|假如|假设|是否|能否|会不会|不确定|不知道|不是|并非|没(?:有)?说|以前|过去|朋友|别人|建议|打算|想改',clause):continue
        subjects=r'(?:我(?:的)?(?:实际做的)?深蹲|(?:原)?计划(?:里|中)?的深蹲|深蹲变式'+('|深蹲' if stored else '')+')'
        for pattern in [subjects+r'(?:是|为|采用|用的是)'+_VARIANT,r'我(?:现在|实际|一直)?做的(?:是)?'+_VARIANT]:
            for match in re.finditer(pattern,clause):
                if not re.match(r'\s*(?:吗|么|[?？])',clause[match.end():]):quotes.append(match.group())
    return quotes


def _specified(text,*,stored=False):
    return bool(_confirmed_squat(text,stored=stored))


def _within_variant_choice(tail, position, *, question):
    # Keep the alternatives in one question even across commas/dashes. An
    # affirmative continuation still owns its predicates, despite a final ?.
    if not question or '还是' not in tail:return False
    if not re.match(r'\s*[:：]?\s*(?:(?:你)?(?:是)?(?:怎么|如何)|(?:用|采用)?哪种|(?:你)?是)',tail):return False
    boundary=re.search(r'但是|不过|然而|反正|所以|因此|无论|肯定|一定|必须|但',tail)
    return boundary is None or position<boundary.start()


def _independent_symptom_starts(tail):
    # A reported body-part symptom is not a predicate of the previous action.
    # Also accept onset + trigger + pain (新出现撑地时疼), still requiring
    # a symptom predicate below. Do not detach 深蹲时，右手腕... .
    pattern=(r'[，,](?:而|但|另外|此外|同时)?(?:今天|现在|目前|刚才)?'
             r'(?:(?:我|你|他|她)的?)?(?:左|右|双)?(?:手腕|肩(?:部)?|膝(?:盖)?)'
             r'(?:(?:今天|现在|刚才)?(?:新)?(?:出现|开始)(?:疼痛|疼|痛|(?=撑地|握重物|抬手))|在|只在|只有在|一(?=撑地|握重物|抬手))'
             r'(?=[^，,。！？；\n]*(?:疼|痛|不适)|[、，,。！？；\n]|$)')
    return [m.start() for m in re.finditer(pattern,tail)
            if not re.search(r'(?:时|期间|过程中)\s*$',tail[:m.start()])]


def _coordinated_category_starts(tail):
    # A quantified category is a separate list item, including coordinated
    # descriptions inside it. A named later action or contrast ends that item.
    # A shared affirmative predicate still applies to the earlier action.
    category=(r'[、，,]\s*(?:以及|还有|及|和)(?:任何|所有|其他)'
              r'(?:需要|涉及|(?:抬手|手臂|举手)过头|撑地|握重物)'
              r'(?:(?!但是|不过|然而|可是|却|但|卧推|深蹲)[^。！？；\n]){0,60}?的(?:动作|活动)')
    return [m.start() for m in re.finditer(category,tail)
            if not re.match(r'\s*[、，,]?\s*(?:两者|它们|这些动作)?(?:都|均|也都)(?:需要|涉及|要|必须)',tail[m.end():])]


def _bench_subjects(raw, antecedent):
    subjects=_generic_mentions(raw,'卧推')
    # In "named substitute replaces bench, but the substitute itself ...",
    # bench is an object, not the actor of that following mechanism. A later
    # explicit bench mention is still collected independently.
    subjects=[m for m in subjects if not (
        re.search(_VARIANT+r'(?:虽然|尽管)?(?:把|将)(?:原来(?:的)?|原计划中的)?$',raw[:m.start()])
        and re.match(r'(?:换掉|替换)了[，,](?:但|但是)?(?:动作本身|这个替代动作)',raw[m.end():]))]
    if not subjects and re.fullmatch(r'\s*[-*]?\s*卧推[：:]\s*(?:今天|本周|这周)?(?:先)?(?:暂停|停)',antecedent):
        subjects=list(re.finditer(r'^\s*它',raw))
    # A single explicit bench equipment choice can own the next same-paragraph
    # pronoun. The question waives no later affirmative mechanism predicate.
    if not subjects and re.fullmatch(r'\s*(?:你(?:的|平时)?)?卧推(?:用的是|使用|是)杠铃还是哑铃\s*',antecedent):
        subjects=list(re.finditer(r'^\s*(?:反正|不过|但)?它',raw))
    return subjects


def _squat_findings(text,records,query):
    plans=[data['plan'] for tool,_,data in records
           if tool=='plan.get_active' and data.get('found') is True and data.get('executed') is not False
           and isinstance(data.get('plan'),dict)]
    generic=any(any(isinstance(e,dict) and e.get('exercise_name')=='深蹲' for e in p.get('exercises',[])) for p in plans)
    if not generic:return []
    specified=_specified(query) or any(_specified(p.get('notes'),stored=True) for p in plans)
    found=[]
    names={e.get('exercise_name') for p in plans for e in p.get('exercises',[]) if isinstance(e,dict)}
    other_names=sorted((n for n in names if isinstance(n,str) and n!='深蹲'),key=len,reverse=True)
    previous=None
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group()
        # Mask parenthetical baseline numbers without changing source offsets.
        masked=re.sub(r'[（(][^）)\n]*[）)]',lambda m:' '*len(m.group()),raw)
        subjects=list(re.finditer('深蹲',masked))
        # A plural claim is bound only to an explicit pair in the immediately
        # preceding sentence, in the same paragraph. Never infer a referent
        # from the entire plan or an unrelated earlier mention.
        if not subjects and previous and '\n' not in text[previous.end():sentence.start()]:
            antecedent=_QUOTED.sub('',previous.group())
            generic_subject=any(not re.search(r'高脚杯|哑铃|杠铃(?:前|背)|背杠|徒手',antecedent[:m.start()][-6:]) for m in re.finditer('深蹲',antecedent))
            if generic_subject and sum(n in antecedent for n in other_names)==1:
                subjects=list(re.finditer(r'(?:这|那)?两个动作|两者',masked))
        previous=sentence
        for subject in subjects:
            before=masked[:subject.start()]
            local_before=re.split(r'[，,]|但是|不过|然而|但',before)[-1]
            universal=bool(re.search(r'所有|任何|一切',local_before[-12:]))
            if specified and not universal:continue
            if re.search(r'高脚杯|哑铃|杠铃(?:前|背)|背杠|徒手',before[-6:]) and not universal:continue
            if re.search(r'如果|假如|假设|若|例如|比如|错误示例|反例|请(?:说明|确认)|(?:需要)?先(?:核实|确认)|不能(?:说|断言|认为|认定|把)|无法(?:断言|认定)|没有依据.{0,8}(?:说|断言)|不是说',local_before):continue
            # A near demonstrative may define a named variant rather than
            # assert a mechanism for the user's unspecified plan exercise.
            if not universal and re.search(_VARIANT+r'[^，,。；\n]{0,45}(?:那|它|该变式)(?:仍然|仍|本身)?是\s*$',before):continue
            tail=masked[subject.end():]
            # Commas do not end the action's explanation, but another named
            # action or independent person does. This keeps a bench-press or
            # daily-life predicate from becoming a squat fact.
            boundaries=[m.start() for name in other_names for m in re.finditer(re.escape(name),tail)]
            # The next explicit squat mention owns its predicates, including
            # a named variant definition; an older subject cannot reclaim them.
            boundaries.extend(m.start() for m in re.finditer('深蹲',tail))
            boundaries.extend(m.start() for m in re.finditer(r'[，,](?:而|但|另外|此外)?(?:我|你|他|她)(?!的深蹲)',tail))
            boundaries.extend(m.start() for m in re.finditer(r'[，,](?:而|但|另外|此外)?'+_VARIANT,tail))
            boundaries.extend(_independent_symptom_starts(tail))
            boundaries.extend(_coordinated_category_starts(tail))
            if boundaries:tail=tail[:min(boundaries)]
            question=raw.rstrip().endswith('?') or text[sentence.end():].startswith('？')
            for mechanism in _MECHANISM.finditer(tail):
                if _within_variant_choice(tail,mechanism.start(),question=question):continue
                lead=re.split(r'[，,]|但是|不过|然而|但',tail[:mechanism.start()])[-1]
                after=tail[mechanism.end():]
                # "怎么拿都会用到手腕" asserts universality; 怎么 alone is
                # not an uncertainty qualifier in that construction.
                lead=re.sub(r'(?:怎么|如何|哪种)[^，,]{0,60}(?:都|均)', '',lead)
                if _UNCERTAIN.search(lead):continue
                if re.match(r'(?:的)?(?:方式|环节)?(?:尚|仍|还)?(?:未知|不清楚|不确定|未说明|没说明|没有说明|没有提供|未提供|没给|需要核实|需核实|吗|么)',after):continue
                start=sentence.start()+subject.start();stop=sentence.start()+subject.end()+mechanism.end()
                quoted=next((q for q in _QUOTED.finditer(raw) if q.start()<subject.start()<subject.end()+mechanism.end()<q.end()),None)
                if quoted and re.match(r'(?:这个|这种)?(?:做法|说法|解释)?(?:是|并)?(?:错误|不成立|没有依据|不能确定)',raw[quoted.end():]):continue
                found.append(dict(code='training_variant_unsupported',quote=text[start:stop],start=start,end=stop))
    # Repeated mentions in one explanation may bind the same predicate. Keep
    # its nearest explicit subject rather than duplicating the repair reason.
    by_end={}
    for item in found:
        if item['end'] not in by_end or item['start']>by_end[item['end']]['start']:by_end[item['end']]=item
    return sorted(by_end.values(),key=lambda item:(item['start'],item['end']))


_BENCH_VARIANT=r'(?:平板杠铃卧推|平板哑铃卧推|哑铃卧推|杠铃卧推|上斜卧推)'


def _confirmed_bench(text, *, stored=False):
    if not isinstance(text,str):return []
    quotes=[]
    for clause in re.split(r'[。！；\n]',_QUOTED.sub('',text)):
        if re.search(r'如果|假如|假设|是否|能否|会不会|不确定|不知道|不是|并非|没(?:有)?说|以前|过去|朋友|别人|建议|打算|想改',clause):continue
        subject=r'(?:我(?:的)?(?:实际做的)?卧推|(?:原)?计划(?:里|中)?的卧推'+('|卧推' if stored else '')+')'
        for m in re.finditer(subject+r'(?:是|为|采用|用的是)('+_BENCH_VARIANT+')',clause):
            if not re.match(r'\s*(?:吗|么|[?？])',clause[m.end():]):quotes.append(m.group())
    return quotes


def movement_source_bindings(records, query):
    plans=[(cid,d['plan']) for tool,cid,d in records if tool=='plan.get_active'
           and d.get('found') is True and d.get('executed') is not False and not d.get('error')
           and not d.get('error_code') and isinstance(d.get('plan'),dict)]
    if not plans:return []
    cid,plan=plans[-1];rows=[]
    for index,e in enumerate(plan.get('exercises',[])):
        if not isinstance(e,dict):continue
        name=e.get('exercise_name')
        confirm={'卧推':_confirmed_bench,'深蹲':_confirmed_squat}.get(name)
        user_quotes=confirm(query) if confirm else []
        notes_quotes=confirm(plan.get('notes'),stored=True) if confirm else []
        rows.append({'source_call_id':cid,'source_path':f'#/plan/exercises/{index}',
                     'saved_fields':{k:e[k] for k in ('exercise_name','sets','reps','rest_seconds','recommended_weight_kg') if k in e},
                     'user_variant_quotes':user_quotes,'plan_note_variant_quotes':notes_quotes,
                     'mechanism_facts_in_plan':[],
                     'unknown_from_name_and_weight':['equipment','grip','support','movement_direction','personal_tolerance'],
                     'reference_is_not_personal_variant':True})
    return rows


def _generic_mentions(text, name):
    return [m for m in re.finditer(name,text)
            if not re.search(r'平板|哑铃|杠铃|上斜|高脚杯|背杠|徒手',text[:m.start()][-6:])]


def _pair_antecedent(text):
    text=_QUOTED.sub('',text)
    return bool(re.search(r'(?:原|已存|生效)计划(?:的)?(?:两个|两项)(?:动作|力量训练)',text)
                or (_generic_mentions(text,'卧推') and _generic_mentions(text,'深蹲')))


def _bench_and_pair_findings(text, records, query):
    bindings=movement_source_bindings(records,query)
    names={r['saved_fields'].get('exercise_name') for r in bindings}
    generic_bench=next((r for r in bindings if r['saved_fields'].get('exercise_name')=='卧推'),None)
    # A confirmed dumbbell or unspecified incline variant does not support a
    # barbell predicate either. Only the matching confirmation can exempt it.
    bench_unknown=generic_bench and not any('杠铃卧推' in q for q in
        generic_bench['user_variant_quotes']+generic_bench['plan_note_variant_quotes'])
    squat=next((r for r in bindings if r['saved_fields'].get('exercise_name')=='深蹲'),None)
    pair_unknown=squat and not (squat['user_variant_quotes'] or squat['plan_note_variant_quotes'])
    found=[];previous=None
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group()
        antecedent=previous.group() if previous and '\n' not in text[previous.end():sentence.start()] else ''
        previous=sentence
        if bench_unknown:
            subjects=_bench_subjects(raw,antecedent)
            for subject in subjects:
                before=raw[:subject.start()]
                local_before=re.split(r'[，,]|但是|不过|然而|但',before)[-1]
                if re.search(r'如果|假如|假设|若|例如|比如|反例|错误示例|不能(?:说|断言|认为)|不是说',local_before):continue
                tail=raw[subject.end():]
                boundary=re.search(r'深蹲|卧推|[，,](?:而|但|另外|此外)?(?:我|你|他|她)',tail)
                if boundary:tail=tail[:boundary.start()]
                categories=_coordinated_category_starts(tail)
                if categories:tail=tail[:min(categories)]
                for mechanism in re.finditer(r'(?:平卧)?握杠|(?:手)?(?:握住|握持|撑住|支撑|用的是|使用|是)(?:并握持)?杠(?:铃)?',tail):
                    lead=re.split(r'[，,]|但是|不过|然而|但',tail[:mechanism.start()])[-1]
                    if _UNCERTAIN.search(lead) or re.search(r'不(?:需要|必)|并非|不是|没有依据|无法断言',lead):continue
                    # This copular equipment alternative is not a selected
                    # barbell fact, even before a semicolon or without '?'.
                    # Only this predicate is exempt; later gripping claims stay.
                    if (re.fullmatch(r'(?:用的是|使用|是)杠(?:铃)?',mechanism.group())
                            and re.match(r'\s*还是\s*(?:用)?哑铃',tail[mechanism.end():])):continue
                    if re.match(r'\s*(?:吗|么|[?？])',tail[mechanism.end():]):continue
                    if re.search(r'怎么|如何|哪种|还是|是否',tail) and text[sentence.end():].startswith(('？','?')):continue
                    quoted=next((q for q in _QUOTED.finditer(raw) if q.start()<subject.start()<subject.end()+mechanism.end()<q.end()),None)
                    if quoted and re.match(r'(?:这个|这种)?(?:说法|解释)?(?:是|并)?(?:错误|不成立|没有依据|不能确定)',raw[quoted.end():]):continue
                    a=sentence.start()+subject.start();b=sentence.start()+subject.end()+mechanism.end()
                    found.append(dict(code='training_variant_unsupported',quote=text[a:b],start=a,end=b))
        # Bind a plural only to a generic pair in this or the immediately
        # preceding sentence; do not cross paragraphs or named variants.
        if names!={'卧推','深蹲'} or not pair_unknown:continue
        if not (_pair_antecedent(raw) or _pair_antecedent(antecedent)):continue
        pattern=r'(?:(?:这|那)?两个动作|两者|它们|卧推和深蹲)(?:又|也)?(?:都|分别)(?:涉及|绕不开|需要|要)[^。！？；\n]{0,12}?手部(?:持重/?支撑|支撑|持重|参与)'
        for m in re.finditer(pattern,raw):
            lead=re.split(r'[，,]',raw[:m.start()])[-1]
            if re.search(r'是否|如果|假如|不能说|不确定|未知|可能|不一定',lead):continue
            if re.match(r'\s*(?:吗|么|[?？])',raw[m.end():]):continue
            a=sentence.start()+m.start();b=sentence.start()+m.end()
            found.append(dict(code='training_variant_unsupported',quote=text[a:b],start=a,end=b))
    return found


def _asserted_mechanism(raw, start, end):
    # Only affirmative local clauses. Quoted examples, other people, and a
    # question/unknown suffix are not the assistant's factual assertion.
    lead=re.split(r'[，,]|但是|不过|然而|但',raw[:start])[-1]
    if (_UNCERTAIN.search(lead) or re.search(r'如果|假如|假设|若|例如|比如|反例|错误示例|朋友|别人|他(?:的)?|她(?:的)?|不(?:必|需要)|并非|不是',lead)):
        return False
    if re.match(r'\s*(?:的)?(?:方式|环节)?(?:尚|仍|还)?(?:未知|不清楚|不确定|未说明|需(?:要)?确认|吗|么|[?？])',raw[end:]):return False
    if any(q.start()<=start and end<=q.end() for q in _QUOTED.finditer(raw)):return False
    return True


def _reported_mechanism(query, subject, predicate):
    plain=_QUOTED.sub('',query)
    for part in re.finditer(r'[^，,。！？；\n]+',plain):
        clause=part.group()
        if re.search(r'如果|假如|假设|若|朋友|别人|他|她|以前|过去|不|没|无|是否|可能|[?？]',clause):continue
        m=re.search(re.escape(subject)+r'(?:时|一|也)?(?:需要|会|也会|就)?'+predicate,clause)
        if m and not re.match(r'\s*(?:吗|么)',clause[m.end():]) and not plain[part.end():].startswith('？'):return True
    return False


def _causal_findings(text, records, query):
    bindings=movement_source_bindings(records,query)
    if not bindings:return []
    rows={r['saved_fields'].get('exercise_name'):r for r in bindings}
    bench=rows.get('卧推')
    unknown_hand=bench and not any(re.search(r'杠铃卧推|哑铃卧推',q) for q in bench['user_variant_quotes']+bench['plan_note_variant_quotes'])
    unknown_pair=set(rows)=={'卧推','深蹲'} and any(not (r['user_variant_quotes'] or r['plan_note_variant_quotes']) for r in rows.values())
    # Extract the onset activity from the actual report, not a list of guessed
    # causes. An activity already described as overhead is not this ambiguity.
    onset=[]
    for clause in re.split(r'[，,。！？；\n]',_QUOTED.sub('',query)):
        if re.search(r'如果|假如|假设|朋友|别人|不|没|是否',clause):continue
        m=re.search(r'(?:今天|今日|刚才)([\u4e00-\u9fff]{1,12})后[^，,]{0,12}(?:疼|痛|开始)',clause)
        if m and not re.search(r'高处|举高|过头',m[1]):onset.append(m[1])
    found=[];previous=None
    def add(sentence,a,b):
        a+=sentence.start();b+=sentence.start()
        found.append(dict(code='training_variant_unsupported',quote=text[a:b],start=a,end=b))
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group();following=text[sentence.end():]
        check=raw+('？' if following.startswith('？') else '')
        antecedent=previous.group() if previous and '\n' not in text[previous.end():sentence.start()] else ''
        previous=sentence
        if unknown_hand:
            for subject in _bench_subjects(raw,antecedent):
                if not _asserted_mechanism(check,subject.start(),subject.end()):continue
                tail=raw[subject.end():]
                # End at a distinct action/person; retain an explicit 它 continuation.
                boundary=re.search(r'深蹲|卧推|[，,](?:而|但|另外|此外)?(?:我|你|他|她)|[，,](?!(?:但|它|这|那))[^，,]{1,8}(?=需要|涉及)',tail)
                if boundary:tail=tail[:boundary.start()]
                categories=_coordinated_category_starts(tail)
                if categories:tail=tail[:min(categories)]
                for m in re.finditer(r'手腕(?:承重|负重|支撑)|手部(?:支撑(?:/握持)?|握持|持重)|抓握|握持',tail):
                    a=subject.end()+m.start();b=subject.end()+m.end()
                    lead=re.split(r'[，,]|但是|不过|然而|但',tail[:m.start()])[-1]
                    if not re.search(r'这类|那类|需要|涉及|必须|必然|属于|是|要',lead):continue
                    if _asserted_mechanism(check,a,b):add(sentence,subject.start(),b)
        pair_bound=_pair_antecedent(raw) or _pair_antecedent(antecedent) or bool(re.search(r'原计划(?:练|训练|硬练|先暂停)',antecedent))
        if unknown_pair and pair_bound:
            pattern=r'(?:(?:这|那)?两个动作|两者|它们)(?:恰好|各自|又|也)*(?:都|分别)(?:涉及|绕不开|需要|要)[^，,。！？；\n]{0,12}?(?:手腕(?:承重|负重)|手部(?:支撑|持重|参与)|抓握)'
            trigger=r'(?:(?:这|那)?两个动作|两者|它们)(?:各自|恰好|也)*(?:都|分别)(?:撞上|重现)(?:了)?(?:你(?:现在)?报告的|当前的|你的)?诱因'
            for m in re.finditer(pattern+'|'+trigger,raw):
                if not _asserted_mechanism(check,m.start(),m.end()):continue
                if '诱因' in m.group() and all(_reported_mechanism(query,n,r'(?:疼|痛)') for n in rows):continue
                add(sentence,m.start(),m.end())
        # A high-probability attribution is still a personal mechanism claim.
        # Bind its inverse predicate only to this explicit generic pair or the
        # immediately preceding pair, with an actual mechanism mentioned here.
        if unknown_pair and pair_bound:
            probable=r'(?:卧推(?:和|、|及)深蹲|深蹲(?:和|、|及)卧推|(?:这|那)?两个动作|两者|它们)(?:也|都)?最可能(?:涉及|用到|重现)(?:的)?(?:环节|动作|诱因)'
            for m in re.finditer(probable,raw):
                if not _MECHANISM.search(raw[:m.start()]):continue
                if re.search(r'平板|哑铃|杠铃|上斜|高脚杯|背杠|徒手',raw[max(0,m.start()-6):m.start()]):continue
                local=re.split(r'[，,]|但是|不过|然而|但',raw[:m.start()])[-1]
                if '同事' in local or not _asserted_mechanism(check,m.start(),m.end()):continue
                add(sentence,m.start(),m.end())
        # An overhead trigger does not identify the unspecified bench motion.
        if bench and re.search(r'过头[^，,。]{0,8}(?:疼|痛)',query):
            for subject in _generic_mentions(raw,'卧推'):
                tail=raw[subject.end():]
                if re.search(r'深蹲|过头推举|举高取物',tail):continue
                m=re.search(r'方向上(?:有)?重叠',tail)
                if m and _asserted_mechanism(check,subject.start(),subject.end()) and _asserted_mechanism(check,subject.end()+m.start(),subject.end()+m.end()) and not _reported_mechanism(query,'卧推',r'(?:抬手|手臂)?过头'):
                    add(sentence,subject.start(),subject.end()+m.end())
        for activity in onset:
            if _reported_mechanism(query,activity,r'(?:抬手|手臂|举手)?过头'):continue
            pattern=re.escape(activity)+r'(?:、[^，,。！？；\n]{1,10})?(?:这类)?(?:会重现|需要)(?:“|「)?(?:抬手|手臂|举手)?过头(?:”|」)?'
            for m in re.finditer(pattern,raw):
                if _asserted_mechanism(check,m.start(),m.end()):add(sentence,m.start(),m.end())
    return found


_RELATIVE_LOAD=re.compile(r'(?:对|给)?'+BODY+r'(?:的)?(?:参与(?:程度)?|负荷|负担|压力|受力)(?:相对|比较|较|更|明显|通常|一般|本身)*(?:少|小|低|轻)')


_BODY_LOAD=re.compile(r'(?<!不)(?:需要|依赖|涉及)(?:到)?'+BODY+r'(?:的)?(?:承重|负重|支撑|稳定|发力|受力|参与)|'+BODY+r'(?:的)?(?:需要|必须)(?:承重|负重|支撑|稳定|发力|受力|参与)')

_BODY_RELATION=re.compile(r'(?:和|与|跟|对)'+BODY+r'(?:的)?(?:关系|关联|影响)(?:更|最)(?:为)?(?:直接|密切|大)')

def _unknown_relative_load_findings(text,records,query):
    # A generic plan label supplies neither personal comparative-load nor
    # affirmative body-load evidence. An exact user report remains a source.
    # Confirmed variants remain outside this bounded unknown-variant check;
    # that is not certification of their load or suitability.
    unknown={b['saved_fields'].get('exercise_name') for b in movement_source_bindings(records,query)
             if not (b['user_variant_quotes'] or b['plan_note_variant_quotes'])}
    unknown &= {'卧推','深蹲'}
    found=[]
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group();following=text[sentence.end():]
        check=raw+('？' if following.startswith('？') else '')
        for name in sorted(unknown):
            for subject in _generic_mentions(raw,name):
                # A new named action after a dash owns its assertion. Keep
                # direct denied speech and quoted objects in their old scope.
                base=raw.rfind('——',0,subject.start())
                base=base+2 if base>=0 else 0
                if base and (re.search(r'(?:不能|无法|不要|不应)(?:说|断言|判断|认定|认为)\s*$',raw[:base-2])
                             or any(q.start()<subject.start()<q.end() for q in _QUOTED.finditer(raw))):base=0
                scoped=check[base:]
                lead=re.split(r'[，,]|但是|不过|然而|可是|却|但',raw[base:subject.start()])[-1]
                if re.search(r'不(?:知道|确定|清楚)',lead):continue
                if not _asserted_mechanism(scoped,subject.start()-base,subject.end()-base):continue
                if nonassertive(raw[base:],subject.start()-base,subject.end()-base,'exercise_load_exemption',following):continue
                tail=raw[subject.end():]
                bounds=[m.start() for m in re.finditer(r'卧推|深蹲|[，,](?:而|但|另外|此外)?(?:我|你|他|她)',tail)]
                bounds+=_independent_symptom_starts(tail)+_coordinated_category_starts(tail)
                if bounds:tail=tail[:min(bounds)]
                for claim in sorted([*_RELATIVE_LOAD.finditer(tail),*_BODY_LOAD.finditer(tail),*_BODY_RELATION.finditer(tail)],key=lambda m:m.start()):
                    a=subject.end()+claim.start();b=subject.end()+claim.end()
                    if not _asserted_mechanism(scoped,a-base,b-base):continue
                    if re.match(r'(?:是否成立|是否属实)(?:还|尚|仍)?(?:未知|不清楚|不确定)',raw[b:]):continue
                    if nonassertive(raw[base:],a-base,b-base,'exercise_load_exemption',following):continue
                    if _BODY_LOAD.fullmatch(claim.group()) and _reported_mechanism(query,name,re.escape(claim.group())):continue
                    if (_BODY_RELATION.fullmatch(claim.group())
                            and re.search(r'你(?:报告|说|自述)[“「\"]?\s*$',raw[:subject.start()])
                            and _reported_mechanism(query,name,re.escape(claim.group()))):continue
                    start=sentence.start()+subject.start();end=sentence.start()+b
                    found.append({'code':'training_variant_unsupported','quote':text[start:end],'start':start,'end':end})
    return found



def _bench_variant_name_findings(text,records,query):
    # An attributed noun can assert equipment without a "uses a barbell"
    # predicate. Only bind it to the user's/original/replaced exercise, leaving
    # general definitions and prospective replacement targets separate.
    bindings=movement_source_bindings(records,query);evidence=set();has_bench=False
    for row in bindings:
        name=row['saved_fields'].get('exercise_name','')
        if not isinstance(name,str):continue
        if name=='卧推':
            has_bench=True
            for quote in row['user_variant_quotes']+row['plan_note_variant_quotes']:
                evidence.update(m.group() for m in re.finditer(_BENCH_VARIANT,quote))
        elif re.fullmatch(_BENCH_VARIANT,name):has_bench=True;evidence.add(name)
    if not has_bench:return []
    if '平板杠铃卧推' in evidence:evidence.add('杠铃卧推')
    if '平板哑铃卧推' in evidence:evidence.add('哑铃卧推')
    owner=r'(?:你|您)(?:原来|原先|现在|目前)?(?:的|做的(?:是)?|练的(?:是)?)'
    plan=r'(?:原|已存|生效|当前)计划(?:里|中)?(?:的|是)?'
    original=r'(?:原来|原先|原本)(?:的|是)?'
    source=r'(?:'+owner+'|'+plan+'|'+original+')'
    found=[]
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group();following=text[sentence.end():]
        for name in re.finditer(_BENCH_VARIANT,raw):
            if name.group() in evidence:continue
            lead=re.split(r'[，,]|但是|不过|然而|但',raw[:name.start()])[-1]
            tail=raw[name.end():];quote=r'[“"「]?'
            bound=re.search(source+quote+r'$',lead) or re.search(r'(?:原动作|原计划卧推)[：:]\s*'+quote+r'$',lead)
            replaced=(re.search(r'(?:把|将)(?:'+source+r')?'+quote+r'$',lead)
                      and re.match(r'[”"」]?(?:先|暂时|已经|已|都)?(?:换掉|替换|换成|换为|改成|改为)',tail))
            inverse=re.search(r'(?:替代|替换|换掉)(?:了)?(?:'+source+r')?'+quote+r'$',lead)
            if not (bound or replaced or inverse):continue
            if nonassertive(raw,name.start(),name.end(),'training_variant_unsupported',following):continue
            if (_UNCERTAIN.search(lead) or re.search(r'如果|假如|假设|若|例如|比如|反例|错误示例|不确定|不知道|同事|朋友|别人|(?:他|她)(?:的|原来|原先|做的|练的)',lead)):continue
            if re.match(r'[”"」]?\s*(?:还是|吗|么|[?？]|(?:尚|仍|还)?(?:未知|不清楚|不确定|未确认))',tail):continue
            a=sentence.start()+name.start();b=sentence.start()+name.end()
            found.append(dict(code='training_variant_unsupported',quote=text[a:b],start=a,end=b))
    return found

def variant_grounding_findings(text,records,query):
    findings=_bench_variant_name_findings(text,records,query)+_squat_findings(text,records,query)+_bench_and_pair_findings(text,records,query)+_causal_findings(text,records,query)+_unknown_relative_load_findings(text,records,query)
    return sorted({(f['start'],f['end']):f for f in findings}.values(),key=lambda f:(f['start'],f['end']))
