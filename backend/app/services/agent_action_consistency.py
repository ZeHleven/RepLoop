"""Locate explicit action and count conflicts in sourced training advice.

This finite text check does not infer exercise safety or diagnose symptoms.
Names come from successful plan observations. Quotes and future return phases
remain separate; an unmatched draft is not certified complete or correct.
"""
import re
from app.services.agent_symptom_context import current_symptom_quotes

_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
_RETURN_HEADING = re.compile(r'(?:之后|后续|以后|好转后|恢复后|疲劳和.{0,8}恢复后)?(?:怎么|如何|什么时候|何时)?(?:逐步)?(?:回归|恢复)(?:条件|步骤|安排|原计划|训练)?(?:[（(][^）)]{0,35}[）)])?[：:]?')
_CURRENT_HEADING = re.compile(r'^(?:现在|当前|这周|本周|这一周|一周内|临时|在补充信息之前).{0,18}(?:安排|调整|怎么练|怎么处理)')
_SOURCE_HEADING = re.compile(r'(?:(?:你(?:的)?)?(?:已保存|已经保存|已存|当前生效)(?:的)?(?:资料|计划|训练计划|记录)(?:现状)?|你的计划现状)(?:[（(][^）)\n]{0,64}[）)])?[：:]?')
_VISUAL_HEADING = re.compile(r'\s*(?:#{1,6}\s+[^\n]+|(?:\d+[.、]\s*)?\*\*[^*\n]{1,64}\*\*)\s*')
_CURRENT_INSTRUCTION = re.compile(r'现在|当前建议|实际执行|照做|(?:这周|本周|今天|接下来).{0,12}(?:安排|做|练)|(?:建议|请|先|继续|照常|仍然)(?:你)?(?:做|练)|改为|降到|减到|保留')


def _conditional_return(text):
    # Explicit symptom/function prerequisites, not "depends on your reaction".
    return bool(re.search(
        r'(?:等|待|只有|如果|若|当|在).{0,55}(?:症状.{0,8}(?:改善|好转|稳定)|日常.{0,8}(?:正常|恢复)|'
        r'不再疼|不疼|不痛|无痛|不引发.{0,8}症状|评估.{0,8}(?:允许|可以))'
        r'.{0,28}(?:后|才|再|时|前提|可以)',text))


def _painless_trial_condition(text, names):
    """A hypothesis about doing an unconfirmed action is not recovery evidence."""
    if names and re.search(
            r'(?:如果|若|只要)[^，,。！？；]{0,12}(?:做|练|活动|热身)'
            r'[^，,。！？；]{0,14}(?:不疼|不痛|无痛|减轻)', text):
        # Testing an action to discover tolerance is not established recovery.
        return True
    for name in names:
        pattern=(r'(?:如果|若|只要)[^，,。！？；]{0,10}'+re.escape(name)
                 +r'[^，,。！？；]{0,14}(?:不疼|不痛|无痛|不加重)')
        for match in re.finditer(pattern,text):
            if not re.search(r'确认|核实|恢复|日常',match.group()):return True
    return False


def _noninstruction(text):
    return bool(re.search(r'错误(?:示例|说法)|反例|不要照(?:这|此)|不能(?:这样|写成)|例如有人(?:说|写)|假设有人',text))


def _pause_span(sentence, name, names=()):
    n=re.escape(name)
    patterns=[
        rf'(?:先停掉|先停止|先暂停|暂停|先别做|这周先不做)[^，,。！？；;\n]{{0,20}}?{n}',
        rf'{n}[^，,。！？；\n]{{0,30}}?(?:先停|暂停|先别做|先不做|这周不做)',
        rf'{n}(?:是|需要)[^，,。！？；\n]{{0,35}}[，,][^。！？；\n]{{0,25}}?先停掉(?:它|该动作)',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern,sentence):
            # A stop verb cannot acquire the next independent subject. A
            # shared list (暂停卧推和深蹲) is different from a contrast
            # (暂停卧推而深蹲保留), even when the comma is omitted.
            matched=m.group()
            if re.search(r'保留|照常|继续做|但是|不过|然而|而|但',matched):
                continue
            other_names=[other for other in names if other!=name and other in matched]
            if other_names:
                between=matched
                for exercise in sorted({name,*other_names},key=len,reverse=True):
                    between=between.replace(exercise,'')
                between=re.sub(r'先停掉|先停止|先暂停|暂停|先别做|这周先不做|先停|先不做|这周不做','',between)
                if not re.fullmatch(r'[\s、和与及都一起先这周本周]*',between):
                    continue
            before=sentence[max(0,m.start()-12):m.start()]
            if (re.search(r'(?:不用|不必|无需|不要|不能|不需要).{0,4}$',before)
                    or re.search(r'(?:不用|不必|无需|不要|不能|不需要).{0,4}(?:先停|暂停|先别做|先不做)',m.group())):
                continue
            return m.span()
    return None



def _symptom_branch_conflicts(text, names):
    """Review explicit symptom-conditioned continuation, not a diagnosis.

    A recovered user stays recovered. It is the answer's hypothetical branch,
    not the user's current state, that conflicts with a continue-training order.
    """
    if not names:return []
    from app.services.agent_assertion_scope import BODY
    symptom=r'(?:疼痛|酸胀|不适|不舒服|肿胀|麻木|疼|痛)'
    pain=re.compile(BODY+r'[^，,。！？；\n]{0,12}?'+symptom+r'|症状(?:再次|又)?(?:复发|出现|加重)')
    negative=re.compile(r'(?:没有|没|不再|不|无)(?:任何|明显)?'+symptom)
    named='(?:'+'|'.join(re.escape(n) for n in sorted(names,key=len,reverse=True))+')'
    action=re.compile(named+r'\s*[：:]?\s*(?:(?:只|先|可|可以)?(?:做|保留|改为|降到|减到)\s*)?(?:[1-9]\d*|[一二两三四五六七八九十])\s*组'
                      r'|(?:继续|接着|照常)(?:练|训练)|(?:训练)?组数(?:减半|减少)')
    found=[]
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group();masked=_QUOTED.sub(lambda m:' '*len(m.group()),raw)
        if _noninstruction(masked) or re.search(r'朋友|别人|他(?:的)?|她(?:的)?',masked):continue
        condition=None
        for clause in re.finditer(r'[^，,]+',masked):
            value=clause.group()
            symptoms=[m for m in pain.finditer(value) if not negative.search(m.group())
                      and not re.match(r'(?:已|已经|完全)?(?:消失|恢复)',value[m.end():])]
            # Every new conditional starts its own branch. A later symptom-free
            # or recovery condition cannot be confused with the painful branch.
            explicit=re.search(r'如果|若|一旦|只有|(?:等|待)(?:症状|疼痛|日常|身体|手腕|膝盖|恢复|好转)',value)
            past=re.search(r'上周|昨天|以前|过去|上次|曾经|曾做',value)
            if explicit or (symptoms and re.search(r'时|的话|就',value)):
                condition=(clause.start(),clause.start()+symptoms[-1].end()) if symptoms and (explicit or not past) else None
            if condition is None:continue
            for match in action.finditer(value):
                if clause.start()+match.start()<condition[1]:continue
                prefix=value[:match.start()];suffix=value[match.end():]
                if re.search(r'(?:不要|不能|不应|不建议|不做|不安排|不是|并非)[^，,]{0,20}$',prefix):continue
                if re.match(r'\s*(?:也)?(?:不要|不能|不做|不安排|不合适|吗|么)',suffix):continue
                if re.search(r'原计划|已存计划|已保存|历史记录',prefix) and not _CURRENT_INSTRUCTION.search(prefix):continue
                start=sentence.start()+condition[0];end=sentence.start()+clause.start()+match.end()
                found.append({'code':'training_action_conflict','quote':text[start:end],'start':start,'end':end})
    return found




def _repetition_progression_conflicts(text, names):
    """Compare explicit per-set counts in a temporary plan and return steps.

    Saved-plan counts are not the temporary selection. Unnamed steps bind only
    when every retained action has the same range; otherwise no numeric fact
    is inferred. Recognized explicit changes advance that action's state.
    """
    if not names:return []
    quantities={name:re.compile(re.escape(name)+r'\s*[：:]?\s*(?:先做|只做|做)?\s*(?:[1-9]\d*\s*组\s*(?:[×xX*]|[，,]?\s*每组)|每组)\s*(\d{1,2})(?:\s*[-~～—–至到]\s*(\d{1,2}))?\s*次') for name in names}
    progression=re.compile(r'从\s*(\d{1,2})\s*次\s*(?:逐步|慢慢)?(?:往|向|恢复到|增加到|加到|提高到|降到|减到|到|至)\s*(\d{1,2})\s*次')
    reset=re.compile(r'(?:降|减|改)(?:到|为)\s*(\d{1,2})\s*次')
    states={};phase=None;spans=set()
    for line in re.finditer(r'[^\n]+',text):
        heading=line.group().replace('*','').strip().strip('# -0123456789.、 ')
        visual=bool(_VISUAL_HEADING.fullmatch(line.group()) or re.fullmatch(r'[^。！？；]{1,30}[：:]',heading))
        if _SOURCE_HEADING.fullmatch(heading):phase='source'
        elif re.search(r'回归|恢复后',heading) and (visual or re.match(r'^(?:之后|逐步)?回归[：:]',heading)):phase='return'
        elif re.search(r'(?:临时|本周|这周|当前|这一周).{0,14}(?:安排|方案|建议|省时版)',heading) and visual:phase='temporary'
        elif visual and not (phase=='return' and re.match(r'以|按|等|待|一次|在',heading)):phase=None
        for sentence in re.finditer(r'[^。！？；]+',line.group()):
            raw=re.sub(r'\*\*','  ',_QUOTED.sub(lambda m:' '*len(m.group()),sentence.group()))
            offset=line.start()+sentence.start()
            if _noninstruction(raw) or re.search(r'朋友|别人|历史记录|历史描述|以前|曾经',raw):continue
            if phase=='temporary':
                for name,pattern in quantities.items():
                    for m in pattern.finditer(raw):
                        prefix=re.split(r'[，,]',raw[:m.start()])[-1]
                        if re.search(r'原计划|已存|已保存|不要|不能|不做|不是|假设|如果|若',prefix):continue
                        low=int(m[1]);high=int(m[2] or m[1])
                        if 1<=low<=high<=30:states[name]=(low,high,(offset+m.start(),offset+m.end()))
            if phase!='return' and not re.search(r'回归|恢复后',raw):continue
            steps=list(progression.finditer(raw));ops=[(m,True) for m in steps]
            ops.extend((m,False) for m in reset.finditer(raw) if not any(t.start()<=m.start()<m.end()<=t.end() for t in steps))
            local=dict(states)
            for m,transition in sorted(ops,key=lambda item:item[0].start()):
                before=raw[:m.start()];clause=re.split(r'[，,]',before)[-1]
                if re.search(r'不要|不能|不是|不必|无需|每周|一周|频率|频次|天数|训练次数',clause):continue
                if re.match(r'\s*(?:吗|么)',raw[m.end():]):continue
                hits=sorted((n.start(),name) for name in names for n in re.finditer(re.escape(name),before))
                targets=[hits[-1][1]] if hits else list(local) if len({v[:2] for v in local.values()})==1 else []
                if not transition and not (hits or re.search(r'每组|次数',before)):continue
                start=int(m[1]);end=int(m[2]) if transition else start
                if not 1<=start<=30 or not 1<=end<=30:continue
                for name in targets:
                    if name not in local:continue
                    low,high,origin=local[name];span=(offset+m.start(),offset+m.end())
                    if transition and not low<=start<=high:
                        spans.update([origin,span])
                    else:local[name]=(end,end,span)
            # A conditional decrease changes only that branch, not the default
            # starting point for later unconditional recovery instructions.
            if not re.search(r'如果|若|假如',raw):states=local
    return [dict(code='training_action_conflict',quote=text[a:b],start=a,end=b) for a,b in sorted(spans)]


def _return_count_bounds(text, records):
    """Check explicit recovery counts against unambiguous saved action rows.

    This is a finite contradiction check, not a prescription validator. Source
    counts are ceilings, never a replacement for the actual temporary choice.
    Conditional alternatives cannot change the following default sequence.
    """
    plans=[d.get('plan') for tool,_,d in records if tool=='plan.get_active'
           and d.get('found') is True and d.get('executed') is not False]
    if len(plans)!=1 or not isinstance(plans[0],dict) or plans[0].get('is_active') is not True:return []
    rows=plans[0].get('exercises')
    if not isinstance(rows,list):return []
    limits={};seen=set();ambiguous=set()
    for row in rows:
        if not isinstance(row,dict):return []
        name=row.get('exercise_name');sets=row.get('sets')
        if not isinstance(name,str) or not 1<len(name)<=40:return []
        if name in seen:ambiguous.add(name)
        seen.add(name)
        reps=re.fullmatch(r'(\d{1,2})(?:[-–~～](\d{1,2}))?',str(row.get('reps','')))
        if type(sets) is int and 1<=sets<=10 and reps and 1<=int(reps[1])<=int(reps[2] or reps[1])<=30:
            limits[name]=(sets,int(reps[2] or reps[1]))
    for name in ambiguous:limits.pop(name,None)
    if not limits:return []
    names=set(limits)
    quantities={n:re.compile(re.escape(n)+r'\s*[：:]?\s*(?:先做|只做|做)?\s*(\d{1,2})\s*组\s*[×xX*]\s*(\d{1,2})(?:\s*[-~～—–至到]\s*(\d{1,2}))?\s*次') for n in names}
    change=re.compile(r'(?:加到|增加到|提高到|恢复到|降到|减到|改为)\s*(\d{1,2})\s*(组|次)')
    rise=re.compile(r'从\s*(\d{1,2})\s*次\s*(?:逐步|慢慢)?(?:往上加|向上加)')
    complete=re.compile(r'(?:已|已经)?(?:回到|恢复到)原计划的?组数|组数(?:已|已经)(?:回到|恢复)(?:了)?原计划')
    states={};phase=None;section_names=set(names);spans=set()
    def targets(prefix, default):
        hits=[(m.start(),m.end(),n) for n in seen for m in re.finditer(re.escape(n),prefix)]
        hits.sort()
        if not hits:return default
        selected={hits[-1][2]}
        for previous,later in zip(reversed(hits[:-1]),reversed(hits[1:])):
            if not re.fullmatch(r'[\s、和与及]*',prefix[previous[1]:later[0]]):break
            selected.add(previous[2])
        return selected & names
    for line in re.finditer(r'[^\n]+',text):
        heading=line.group().replace('*','').strip().strip('# -0123456789.、 ')
        visual=bool(re.match(r'\s*(?:#{1,6}\s+|\*\*[^*\n]{1,64}\*\*)',line.group()) or re.fullmatch(r'[^。！？；]{1,35}[：:]',heading))
        if _SOURCE_HEADING.fullmatch(heading):phase='source'
        elif visual and re.search(r'回归|恢复后|恢复条件',heading):
            phase='return';section_names={n for n in names if n in heading} or set(names)
        elif visual and re.search(r'(?:临时|本周|这周|当前|这一周).{0,14}(?:安排|方案|建议|省时版|减量版)',heading):phase='temporary'
        elif visual and not (phase=='return' and re.match(r'以|按|等|待|一次|在|前提|满足|(?:先|再|最后)?(?:组数|次数|负重|重量)',heading)):phase=None
        if phase not in {'temporary','return'}:continue
        for sentence in re.finditer(r'[^。！？；]+',line.group()):
            raw=re.sub(r'\*\*','  ',_QUOTED.sub(lambda m:' '*len(m.group()),sentence.group()))
            offset=line.start()+sentence.start();local=dict(states)
            if _noninstruction(raw) or re.search(r'朋友|别人|同事|历史记录|历史描述|以前|曾经',raw):continue
            for clause in re.finditer(r'[^，,、]+',raw):
                value=clause.group();base=offset+clause.start()
                events=[(m,'quantity',n) for n,p in quantities.items() for m in p.finditer(value)]
                if phase=='return':events += [(m,'change',None) for m in change.finditer(value)]+[(m,'rise',None) for m in rise.finditer(value)]+[(m,'complete',None) for m in complete.finditer(value)]
                for m,kind,name in sorted(events,key=lambda e:e[0].start()):
                    prefix=re.split(r'但是|不过|然而|但',value[:m.start()])[-1];suffix=value[m.end():]
                    if re.search(r'不要|不能|不应|不是|不必|无需|是否|会不会|每周|一周|频率|频次|天数|训练次数',prefix):continue
                    if re.match(r'\s*(?:吗|么|[?？])',suffix) or (not suffix.strip() and text[base+m.end():].startswith(('？','?'))):continue
                    if re.match(r'(?:这个|这种)?(?:说法|做法)?(?:不合适|不成立|不对|是错误的)',suffix):continue
                    span=(base+m.start(),base+m.end())
                    if kind=='quantity':
                        if re.search(r'原计划|已存|已保存|假设',prefix):continue
                        counts=(int(m[1]),int(m[2]),int(m[3] or m[2]))
                        if not 1<=counts[0]<=10 or not 1<=counts[1]<=counts[2]<=30:continue
                        if phase=='temporary':
                            if not re.search(r'如果|若|假如',raw):local[name]=counts
                        else:
                            if counts[0]>limits[name][0] or counts[2]>limits[name][1]:spans.add(span)
                            local[name]=counts
                        continue
                    selected=targets(prefix,section_names)
                    if kind=='complete':
                        if not re.search(r'此时|这时|至此|现在|已|已经',prefix+m.group()):continue
                        if selected==names and seen!=names:continue
                        if selected and selected<=local.keys() and any(local[n][0]!=limits[n][0] for n in selected):spans.add(span)
                        continue
                    unit=m[2] if kind=='change' else '次';target=int(m[1])
                    if unit=='次' and len({local[n][1:] for n in selected if n in local})>1 and not any(n in prefix for n in names):continue
                    for n in selected:
                        if n not in local:continue
                        count,low,high=local[n]
                        if kind=='rise':
                            if low<=target<=high and target>=limits[n][1]:spans.add(span)
                        elif unit=='组':
                            if target>limits[n][0]:spans.add(span)
                            local[n]=(target,low,high)
                        else:
                            if target>limits[n][1]:spans.add(span)
                            local[n]=(count,target,target)
            if not re.search(r'如果|若|假如',raw):states=local
    return [dict(code='training_action_conflict',quote=text[a:b],start=a,end=b) for a,b in sorted(spans)]


def _return_load_conflicts(text, names, query):
    """Keep a stated return-load basis separate from the saved prescription.

    Only paired, explicit hold/defer instructions in the same return section
    are checked. A selected start or an explicit intervening reset owns the
    following hold; conditional choices do not rewrite the default branch.
    """
    if not names:return []
    load=r'(?:轻重量|轻负重|可耐受的(?:轻)?(?:重量|负重)|\d+(?:\.\d+)?\s*(?:kg|公斤|千克))'
    choice=re.compile(r'(?:起步|开始|先|采用|选择|选定|选用)[^，,。！？；\n]{0,16}?'+load+'|'+load+r'[^，,。！？；\n]{0,8}?(?:作为|是)(?:回归)?(?:起点|起步负重|起步重量)',re.I)
    hold=re.compile(r'(?:负重|重量)(?:都|先|仍)?(?:保持|维持)(?:原样|不变)|(?:保持|维持)(?:原计划|原处方|原来|已存)(?:的)?(?:负重|重量)')
    defer=re.compile(r'(?:最后(?:才|再)?|(?:之后|随后)(?:才|再)|(?:次数|组数)(?:稳定|恢复)后(?:才|再))(?:小幅|逐步)?(?:试|恢复|增加|加)(?:回)?(?:负重|重量)')
    user_selected=set();plain_query=_QUOTED.sub('',query)
    for part in re.finditer(r'[^，,。！？；\n]+',plain_query):
        clause=part.group()
        if (re.search(r'我(?:已经|已|实际|现在)',clause) and choice.search(clause)
                and not plain_query[part.end():].startswith('？')
                and not re.search(r'如果|假如|假设|朋友|别人|是否|不(?:确定|知道|想|打算|选|用)|没(?:有)?(?:选|确定|采用|用)|未(?:选|确定)|吗|么|[?？]',clause)):
            user_selected.update(n for n in names if n in clause)
    selected=set(user_selected);held={};phase=None;section_names=set(names);spans=set()
    def targets(prefix):
        hits=sorted((m.start(),n) for n in names for m in re.finditer(re.escape(n),prefix))
        return {hits[-1][1]} if hits else section_names
    for line in re.finditer(r'[^\n]+',text):
        heading=line.group().replace('*','').strip().strip('# -0123456789.、 ')
        visual=bool(_VISUAL_HEADING.fullmatch(line.group()) or re.fullmatch(r'[^。！？；]{1,35}[：:]',heading))
        if visual:
            if re.search(r'回归|恢复后|再恢复|恢复条件',heading):
                phase='return';held={};section_names={n for n in names if n in heading} or set(names)
            elif re.search(r'临时|本周|这周|当前',heading):phase='temporary';held={}
            elif not (phase=='return' and (re.match(r'以|按|等|待|一次|在|前提|满足',heading)
                    or re.fullmatch(r'(?:先|再|最后|随后)?(?:恢复|稳定)?(?:组数|次数|负重|重量)(?:稳定)?(?:后|阶段|安排)?[：:]?',heading)
                    or defer.fullmatch(heading))):
                phase=None;held={};selected=set(user_selected)
        for sentence in re.finditer(r'[^。！？；]+',line.group()):
            raw=_QUOTED.sub(lambda m:' '*len(m.group()),sentence.group());offset=line.start()+sentence.start()
            if _noninstruction(raw) or re.search(r'朋友|别人|历史记录|以前|曾经',raw):continue
            in_return=phase=='return' or bool(re.search(r'回归时|恢复后',raw))
            if not in_return and phase!='temporary':continue
            local_selected=set(selected);local_held=dict(held)
            ops=[(m,'choice') for m in choice.finditer(raw)]
            if in_return:ops += [(m,'hold') for m in hold.finditer(raw)]+[(m,'defer') for m in defer.finditer(raw)]
            for m,kind in sorted(ops,key=lambda x:x[0].start()):
                lead=re.split(r'[，,]|但是|不过|然而|但',raw[:m.start()])[-1]
                if re.search(r'(?:不要|不能|不应|不必|无需|不建议|不是|不能说|是否|会不会)[^，,]{0,8}$',lead):continue
                if re.match(r'\s*(?:吗|么|[?？])',raw[m.end():]) or (m.end()==len(raw) and text[offset+m.end():].startswith('？')):continue
                if re.match(r'(?:这个|这种)?(?:说法|做法)?(?:不合适|不成立|不对|是错误的|不应)',raw[m.end():]):continue
                target=targets(raw[:m.end()]);span=(offset+m.start(),offset+m.end())
                if kind=='choice':
                    if re.search(r'原计划|已存计划|已保存计划',lead):continue
                    local_selected.update(target)
                    for name in target:local_held.pop(name,None)
                elif kind=='hold':
                    for name in target:
                        if name not in local_selected or re.search(r'原计划|原处方|已存',m.group()):local_held[name]=span
                else:
                    for name in target:
                        if name in local_held:spans.update([local_held[name],span])
            if not re.search(r'如果|若|假如',raw):selected=local_selected;held=local_held
    return [dict(code='training_action_conflict',quote=text[a:b],start=a,end=b) for a,b in sorted(spans)]


def _pending_clarifications(text, names, unknown_variants=()):
    """Bind explicit unknown movement details to a named plan action.

    A nearby same-paragraph subject may own '不确定你平时是哪种'. An
    unrelated unknown time, source description or another person's question
    is not a prerequisite. This is a finite contradiction check, not triage.
    """
    detail=r'(?:哪种|怎么做|怎么练|变式|握法|姿势|支撑|做法)'
    unknown=re.compile(r'(?:不确定|不清楚|不知道|没(?:有)?说明|还没说|尚未(?:确认|明确)|未确认|待确认|请补充)[^。！？；\n]{0,22}?'+detail
                       +'|'+detail+r'[^，,。！？；\n]{0,8}?(?:尚未确认|还没确认|未确认|待确认|未知|不清楚|没说明)')
    # Missing saved fields do not erase a variant already supplied by the user.
    missing=re.compile(r'没(?:有)?(?:写明|写|记录)[^，,。！？；\n]{0,16}?'+detail
                       +'|'+detail+r'[^，,。！？；\n]{0,8}?没(?:有)?(?:记录|写明|写)')
    def last_group(subjects, raw):
        if not subjects:return set()
        group={subjects[-1][2]}
        for left,right in zip(reversed(subjects[:-1]),reversed(subjects[1:])):
            if not re.fullmatch(r'[\s、和与及]*',raw[left[1]:right[0]]):break
            group.add(left[2])
        return group
    pending={}
    for paragraph in re.finditer(r'[^\n]+(?:\n(?!\n)[^\n]+)*',text):
        active=set()
        for sentence in re.finditer(r'[^。！？；\n]+',paragraph.group()):
            raw=_QUOTED.sub(lambda m:' '*len(m.group()),sentence.group())
            if _noninstruction(raw) or re.search(r'朋友|别人|他(?:的|平时)|她(?:的|平时)|假设|如果|假如|以前|曾经|历史记录',raw):continue
            subjects=sorted((m.start(),m.end(),name) for name in names for m in re.finditer(re.escape(name),raw))
            matches=list(unknown.finditer(raw))+list(missing.finditer(raw))
            # A postposed unknown predicate still owns the immediately preceding
            # detail question, not an independent person or unrelated field.
            if not text[paragraph.start()+sentence.end():].startswith(('？','?')):
                matches.extend(re.finditer(detail+r'(?:方式)?(?:如何|怎样)(?:[，,])?(?:都|也|仍)?(?:还没(?:有)?|尚未|未)确认',raw))
            matches.extend(re.finditer(r'能不能(?:保留|做|练)[^。！？；\n]{0,16}取决于[^。！？；\n]{0,12}补充',raw))
            for name in names:
                matches.extend(re.finditer(r'你(?:平时|实际|现在)?'+re.escape(name)+r'[^。！？；\n]{0,10}是[^。！？；\n]{1,16}还是[^。！？；\n]{1,16}',raw))
            for match in matches:
                prior=[s for s in subjects if s[0]<match.end()]
                targets=last_group(prior,raw) if prior else active
                for name in targets:
                    if match.re is missing:
                        lead=re.split(r'[，,]|但是|不过|然而|但',raw[:match.start()])[-1]
                        if name not in unknown_variants or re.search(r'不能说|不是|并非|不代表',lead):continue
                    offset=paragraph.start()+sentence.start()
                    pending.setdefault(name,(offset+match.start(),offset+match.end()))
            if subjects:active=last_group(subjects,raw)
    # A generic saved label cannot justify trying a named variant first and
    # only then discovering whether its posture reproduces the new symptom.
    for line in re.finditer(r'[^\n]+',text):
        raw=_QUOTED.sub(lambda m:' '*len(m.group()),line.group())
        if _noninstruction(raw) or re.search(r'朋友|别人|以前|曾经',raw):continue
        for name in unknown_variants:
            if not re.search(r'(?:杠铃|哑铃|高脚杯|徒手)[^。！？；\n]{0,6}'+re.escape(name),raw):continue
            match=re.search(r'如果(?:这个|该|这种|实际)?(?:姿势|做法|动作)[^。！？；\n]{0,30}(?:疼|痛|不适|不舒服)[^。！？；\n]{0,12}(?:跳过|停|不做)',raw)
            if match:pending.setdefault(name,(line.start()+match.start(),line.start()+match.end()))
    return pending


def action_consistency_findings(text, records, query):
    if not any(tool=='health.get_screening_summary' and data.get('found') is True for tool,_,data in records):
        return []
    names=set()
    for tool,_,data in records:
        if tool!='plan.get_active' or data.get('found') is not True:
            continue
        plan=data.get('plan')
        if not isinstance(plan,dict):
            continue
        for exercise in plan.get('exercises',[]):
            name=exercise.get('exercise_name') if isinstance(exercise,dict) else None
            if isinstance(name,str) and 1<len(name)<=40 and not re.search(r'[\n，,。；;！？!?<>]',name):
                names.add(name)
    from app.services.agent_training_constraints import training_time_findings
    draft_conflicts=_symptom_branch_conflicts(text,names)+_repetition_progression_conflicts(text,names) + _return_count_bounds(text,records)+_return_load_conflicts(text,names,query)+training_time_findings(text,records,query)
    symptomatic=bool(current_symptom_quotes(query))
    stopped={};conflicts=[];future=False
    from app.services.agent_variant_grounding import movement_source_bindings
    unknown_variants={r['saved_fields']['exercise_name'] for r in movement_source_bindings(records,query)
                      if r['saved_fields'].get('exercise_name') in {'卧推','深蹲'}
                      and not (r['user_variant_quotes'] or r['plan_note_variant_quotes'])}
    # An explicit method gap is an execution prerequisite even without pain.
    # A generic plan name alone is still not a reason to question a well user.
    query_pending=_pending_clarifications(query,names|{'动作'},unknown_variants|{'动作'})
    required=unknown_variants if symptomatic else (set(query_pending)&names)
    if not symptomatic and '动作' in query_pending:required|=unknown_variants
    # Inspect the final answer even when query extractors found no prerequisite.
    pending=_pending_clarifications(text,names,unknown_variants)
    # The source gap exists even when the draft omits its own uncertainty.
    # None is a source prerequisite, not an invented quote in the draft.
    for name in required:pending.setdefault(name,None)
    source_section=False;source_content_seen=False;previous_end=0
    for line in re.finditer(r'[^\n]+',text):
        inline_source=bool(re.match(r'\s*(?:\*\*)?(?:已存计划|已保存计划|生效计划)(?:\*\*)?(?:\s*[（(][^）)\n]{0,80}[）)])?(?:是)?[：:]',line.group()))
        heading=line.group().replace('*','').strip().strip('# -0123456789.、 ')
        source_heading=bool(_SOURCE_HEADING.fullmatch(heading) and not _CURRENT_INSTRUCTION.search(heading))
        if source_heading:
            source_section=True;source_content_seen=False;future=False
        elif _VISUAL_HEADING.fullmatch(line.group()) or _CURRENT_HEADING.search(heading) or _RETURN_HEADING.fullmatch(heading):
            source_section=False
        elif (source_section and source_content_seen
              and text[previous_end:line.start()].count('\n')>1
              and not re.match(r'\s*(?:[-*]|\d+[.、])\s',line.group())
              and not line.group().startswith(('  ','\t'))):
            source_section=False  # A new unlabelled prose paragraph is not a source block.
        if source_section and not source_heading:
            # A provenance heading cannot hide an instruction inside it.
            if _CURRENT_INSTRUCTION.search(_QUOTED.sub('',line.group())):
                source_section=False
            else:
                source_content_seen=True
        previous_end=line.end()
        if len(heading)<65:
            if _RETURN_HEADING.fullmatch(heading):
                future=True
            elif _CURRENT_HEADING.search(heading):
                future=False
        active=None # Omitted action names inherit only within this line.
        for sentence in re.finditer(r'[^。！？；]+',line.group()):
            raw=sentence.group();offset=line.start()+sentence.start()
            masked=_QUOTED.sub(lambda m:' '*len(m.group()),raw)
            inherited=active
            subjects=sorted((m.start(),m.end(),name) for name in names for m in re.finditer(re.escape(name),masked))
            if subjects:active=subjects[-1][2]
            if source_section or future or (_conditional_return(masked) and not _painless_trial_condition(masked,pending)) or _noninstruction(masked):
                continue
            for name in sorted(names,key=len,reverse=True):
                stop=_pause_span(masked,name,names)
                if stop:
                    start,end=stop
                    stopped[name]=(offset+start,offset+end)
                # Positive prescribed set counts, not zero or past baseline.
                prescriptions=list(re.finditer(re.escape(name)+r'\s*[：:]?\s*(?:(?:只|先|可|可以)?(?:做|保留|改为|降到|减到)\s*)?([1-9]\d*|[一二两三四五六七八九十])(?:\s*[–—~～到至-]\s*(?:[1-9]\d*|[一二两三四五六七八九十]))?\s*组',masked))
                for candidate in re.finditer(r'(?:只|先|仅)?(?:做|试)\s*(?:[1-9]\d*|[一二两三四五六七八九十])(?:\s*[–—~～到至-]\s*(?:[1-9]\d*|[一二两三四五六七八九十]))?\s*组',masked):
                    if any(p.start()<=candidate.start()<candidate.end()<=p.end() for p in prescriptions):continue
                    prior=[n for a,b,n in subjects if b<=candidate.start()]
                    if (prior[-1] if prior else inherited)==name:prescriptions.append(candidate)
                # Permission to try is a prescription even without a set count.
                # Resolve omitted names only within this line, like numeric sets.
                for candidate in re.finditer(r'(?:可以(?:先)?|先|可)(?:(?:用|以)(?:轻重量|轻负荷|低负荷))?(?:少量|轻量)?(?:试练|试做|试一试|试一下|试试|试(?:[一二两几]?(?:两|三)?(?:下|次))?)(?=$|[\s，,。！？；;：:、）)]|吗|么|(?:这个|这种)?(?:说法|做法|建议))',masked):
                    if any(p.start()<=candidate.start()<candidate.end()<=p.end() for p in prescriptions):continue
                    prior=[n for a,b,n in subjects if b<=candidate.start()]
                    if (prior[-1] if prior else inherited)!=name:continue
                    lead=masked[:candidate.start()];tail=masked[candidate.end():]
                    if re.search(r'朋友|别人|以前|曾经|历史记录|假设',masked):continue
                    if re.search(r'(?:不建议|不允许|禁止|不应|不必|不让|不给[^，,]{0,8}让)[^，,]{0,10}$|不$',lead):continue
                    if (re.match(r'\s*(?:吗|么|[?？])',tail)
                            or (not tail.strip() and text[offset+candidate.end():].startswith(('？','?')))
                            or re.match(r'(?:这个|这种)?(?:说法|做法|建议)?(?:不成立|不合适|不对|是错误的)',tail)):continue
                    prescriptions.append(candidate)
                    if name in unknown_variants:pending.setdefault(name,None)
                # "Can do ... <action>" grants execution without numeric sets.
                # The qualifier and source/negative checks below still apply.
                for candidate in re.finditer(r'(?:可以|建议|先)(?:只)?(?:做|练)[^。！？；\n]{0,28}?'+re.escape(name),masked):
                    if re.search(r'朋友|别人|以前|曾经|历史记录|假设',masked):continue
                    if re.search(r'(?:不建议|不允许|禁止|不应|不必|不让)[^，,]{0,10}$|不$',masked[:candidate.start()]):continue
                    if any(p.start()<=candidate.start()<candidate.end()<=p.end() for p in prescriptions):continue
                    if text[offset+candidate.end():].startswith(('？','?')):continue
                    prescriptions.append(candidate)
                    if name in unknown_variants:pending.setdefault(name,None)
                if not prescriptions or name not in stopped and name not in pending:continue
                for prescription in sorted(prescriptions,key=lambda p:p.start()):
                    start,end=offset+prescription.start(),offset+prescription.end()
                    # Only the prefix up to this count owns its source/current
                    # status. Earlier source counts cannot hide later trial orders.
                    # A provenance label can share a line with several source
                    # sentences. A later current instruction ends this exemption.
                    line_prefix=_QUOTED.sub('',line.group()[:sentence.start()+prescription.end()])
                    if inline_source and not _CURRENT_INSTRUCTION.search(line_prefix):continue
                    through=masked[:prescription.end()]
                    if re.search(r'原计划|生效计划|已存计划|已保存计划|保存的计划|当前计划|原来',through) and not re.search(r'临时|改为|降到|减到|(?:这周|本周|今天|现在|当前).{0,12}(?:安排|做|试)|(?:但|不过|然而)[^，,]{0,16}(?:做|试)',through):
                        continue
                    before=masked[:prescription.start()]
                    if re.search(r'(?:不要|不能|不做|不安排|不是|并非)[^，,]{0,12}$',before):
                        continue
                    if name in pending:
                        after=masked[prescription.end():]
                        conditional=re.search(r'(?:等|待|先|只有|在|如果).{0,16}(?:确认|核实|明确).{0,48}(?:变式|姿势|握法|支撑|做法|不诱发|不疼|耐受).{0,24}(?:后|才|再)',before)
                        qualified=conditional and not re.search(r'但|不过|然而|今天先|现在就',before[conditional.end():])
                        noninstruction=re.search(r'历史记录|曾经|时间估算|估算示例|计算示例|假设|按[^。！？；]{0,24}算',masked) and not _CURRENT_INSTRUCTION.search(masked)
                        if not (qualified or noninstruction or re.match(r'\s*(?:也)?(?:不能|不要|不做|不安排|可以吗|吗|么)',after)):
                            if pending[name] is not None:conflicts.append(pending[name])
                            conflicts.append((start,end))
                    if name in stopped and start>=stopped[name][1]:
                        conflicts.extend([stopped[name],(start,end)])
    return draft_conflicts+[{'code':'training_action_conflict','quote':text[s:e],'start':s,'end':e}
            for s,e in sorted(set(conflicts))]
