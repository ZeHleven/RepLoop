"""Source quotations for current symptoms, never a diagnosis or prescription.

This adds a generation contract without replacing the original task or hiding
its remaining goals. Unrecognized wording keeps the regular assessment path.
"""
import json
import re
import hashlib
from app.services.agent_assertion_scope import CLINICAL_UNCERTAINTY_CONTRACT

_SYMPTOM = re.compile(r'疼(?:痛)?|酸痛|不适|不舒服|肿胀|麻木|发麻')
_CURRENT = re.compile(r'今天|今日|现在|刚(?:刚)?|突然|开始|新出现|这两天|这几天')


def current_symptom_quotes(query):
    if not isinstance(query, str):
        return []
    # Quoted examples and other people's symptoms are not the user's report.
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', query)
    quotes=[]
    for clause in re.split(r'[，,。；;！？!?\n]', text):
        if not (_CURRENT.search(clause) and _SYMPTOM.search(clause)):
            continue
        if re.search(r'如果|假如|假设|比如|例如|朋友|别人|他(?:的|今天)|她(?:的|今天)', clause):
            continue
        positive = re.sub(r'(?:没有|没|不再|不|无)(?:任何|明显)?(?:疼痛|疼|酸痛|不适|不舒服|肿胀|麻木|发麻)', '', clause)
        if not _SYMPTOM.search(positive) or re.search(r'已(?:经)?恢复|已经好了|已痊愈', clause):
            continue
        quote=clause.strip()
        if quote and quote in query:
            quotes.append(quote)
    if not quotes:
        # An explicitly continuing symptom may name onset in the prior clause.
        # Bind only adjacent clauses in the same unquoted, personal episode;
        # "今天还在加班" is not a continuation of pain.
        for episode in re.split(r'[。；;！？!?\n]',text):
            if re.search(r'如果|假如|假设|比如|例如|朋友|同事|别人|他(?:的|今天)|她(?:的|今天)',episode):continue
            for match in re.finditer(
                    r'([^，,]+)[，,]\s*(?:今天|今日|现在|目前)\s*'
                    r'(?:还在|仍在|仍未缓解|还没好|还没有好|仍持续)(?=[，,]|$)',episode):
                prior=re.sub(r'(?:没有|没|不再|不|无)(?:任何|明显)?(?:'+_SYMPTOM.pattern+')','',match[1])
                if _SYMPTOM.search(prior) and match.group().strip() in query:
                    quotes.append(match.group().strip())
    if not quotes:
        # A follow-up may put onset in the first clause and the trigger in a
        # later one: 今天取高处物品后开始，...，抬手过头才疼. Keep the
        # complete user span; never borrow a symptom from another sentence.
        for episode in re.split(r'[。；;！？!?\n]',text):
            if not re.match(r'\s*(?:补充[：:]\s*)?(?:是)?(?:今天|今日|刚才)[^，,]{0,30}开始[，,]',episode):continue
            if re.search(r'如果|假如|假设|比如|例如|朋友|别人|他(?:的|今天)|她(?:的|今天)|以前|过去|昨天|已(?:经)?恢复|已经好了|已痊愈',episode):continue
            symptom=r'(?:疼痛|疼|酸痛|不适|不舒服|肿胀|麻木|发麻)'
            positive=re.sub(r'(?:没有|没|不再|不|无)(?:任何|明显)?'+symptom+r'(?:(?:[、/]|和|及|或)?'+symptom+r')*','',episode)
            if _SYMPTOM.search(positive) and episode.strip() in query:quotes.append(episode.strip())
    return quotes


SYMPTOM_CLARIFICATION_CONTRACT="""
症状澄清记忆：recognized_current_user_facts是当前用户原文中有限识别的自述，逐条保留部位、侧别与原文位置，不是诊断或完整病史。已说搬东西后开始，就沿用这个起始背景，不重问整个起因；已明确某活动疼或不疼，不再原样询问。起始事件不等于扭伤机制，拿杯子不疼不等于静息不疼；只问尚缺且影响选择的细节。已给痛分沿用原报告，不重问整项程度；明确单一补充未重述部位时，body和side为空表示未绑定具体部位，不得自行填入或用于否定另一部位的澄清。未识别不等于未提供，仍核对完整原文及对话，不能把助手建议当用户事实。
纠正重复提问时保留复合问句中仍未知的部分，例如静息时是否疼、搬东西时是否突然扭到、实际动作变式；不要因局部重复删除全部必要澄清，也不要用重问代替已经能交付的取舍、时限和回归条件。不同部位、另一侧或后来变化分别处理。
"""
_FACT_BODY=re.compile(r'(左|右|双)?(手腕|腕部|腕关节|肩膀|肩部|肩|膝盖|膝|脚踝|踝部)')
_FACT_ACTIVITY=re.compile(r'用力握重物|握重物|撑地|抬手过头|拿水杯|拿杯子|走路')
_FACT_QUOTED=re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')


def _fact_bodies(text):
    canonical={'腕部':'手腕','腕关节':'手腕','肩膀':'肩','肩部':'肩','膝盖':'膝','踝部':'脚踝'}
    return {(canonical.get(m[2],m[2]),m[1] or '') for m in _FACT_BODY.finditer(text)}


def _fact_activity(text):
    return {'用力握重物':'握重物','拿杯子':'拿水杯'}.get(text,text)


def reported_symptom_facts(query):
    """Finite current-user reports with offsets; never infer missing answers."""
    if not isinstance(query,str) or not current_symptom_quotes(query):return []
    plain=_FACT_QUOTED.sub(lambda m:' '*len(m.group()),query)
    # Retain explicit answers in one unlocated follow-up episode. Empty site
    # fields never bind that report to an anatomical site or assistant claim.
    quotes=current_symptom_quotes(query);unlocated=None
    if (len(quotes)==1 and not _fact_bodies(plain)
            and re.match(r'\s*补充[：:]\s*(?:是)?(?:今天|今日|刚才)[^，,]{0,30}开始[，,]',quotes[0])):
        start=query.index(quotes[0]);unlocated=(start,start+len(quotes[0]))
    facts=[];active=None;blocked=False;last=0
    for part in re.finditer(r'[^，,。；;！？!?\n]+',plain):
        raw=part.group();gap=plain[last:part.start()];last=part.end()
        if re.search(r'[。；;！？!?\n]',gap):active=None;blocked=False
        if re.search(r'如果|假如|假设|比如|例如|朋友|别人|他(?:的|今天)|她(?:的|今天)|以前|过去|曾经',raw):blocked=True;active=None
        if blocked:continue
        bodies=_fact_bodies(raw)
        if bodies:active=next(iter(bodies)) if len(bodies)==1 else None
        elif active is None and unlocated and unlocated[0]<=part.start()<part.end()<=unlocated[1]:active=('','')
        if (plain[part.end():].startswith(('？','?')) or re.search(r'不知道|不确定|不清楚|是否|会不会|是不是|没(?:有)?说|未说明|未确认|吗|么',raw)):
            continue
        if active is None:continue
        def add(kind,**values):
            facts.append({'kind':kind,'body':active[0],'side':active[1],**values,
                          'source':'current_user_message','quote':query[part.start():part.end()],
                          'start':part.start(),'end':part.end()})
        onset=re.search(r'(?:今天|今日|刚才|昨天|前天)([\u4e00-\u9fff]{1,12})后',raw)
        if not onset:onset=re.match(r'\s*(?:是)?([\u4e00-\u9fff]{1,12})后',raw)
        if onset and re.search(r'疼|痛|开始',raw[onset.end():]):add('onset_context',activity=onset[1])
        score=re.fullmatch(r'\s*(?:(?:疼痛|疼|痛)(?:程度|评分)?|程度|评分)?(?:大约|大概|约|是|为)?\s*(10|[0-9](?:\.[0-9]+)?)\s*分\s*',raw)
        if score:add('pain_score',score=float(score[1]))
        negative=re.compile(r'(?:不再|不|没(?:有)?|无)(?:明显)?(?:疼痛|疼|痛)')
        neg=bool(negative.search(raw));pos=bool(re.search(r'疼|痛',negative.sub('',raw)))
        # Mixed polarities without clause separation are deliberately unresolved.
        if neg==pos:continue
        for activity in _FACT_ACTIVITY.finditer(raw):add('activity_pain',activity=_fact_activity(activity.group()),pain=pos)
    return facts


def symptom_clarification_findings(text,query):
    facts=reported_symptom_facts(query)
    if not facts:return []
    sites={(f['body'],f['side']) for f in facts}
    for quote in current_symptom_quotes(query):sites.update(_fact_bodies(quote))
    onset=re.compile(r'(?:(?:左|右|双)?(?:手腕|腕部|腕关节|肩膀|肩部|肩|膝盖|膝|脚踝|踝部))?(?:疼痛|疼|痛)(?:是)?怎么(?:开始|出现|发生)(?:的)?|有没有(?:明确的)?(?:诱因|起因)')
    pain=re.compile('(?P<activity>'+_FACT_ACTIVITY.pattern+r')(?:时|的时候)?(?:疼不疼|痛不痛|会不会(?:疼|痛)|是否(?:会)?(?:疼|痛)|会(?:疼|痛)吗)')
    severity=re.compile(r'(?:(?:疼痛|疼|痛)(?:的)?(?:程度|评分)?|程度|评分)(?:大概|大约|约)?(?:是|有)?(?:多少|几)分')
    found=[]
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=_FACT_QUOTED.sub(lambda m:' '*len(m.group()),sentence.group())
        asked=text[sentence.end():].startswith('？') or raw.rstrip().endswith('?') or re.search(r'请(?:告诉|说明|补充)|需要(?:你)?(?:补充|确认)|想(?:问|确认)',raw)
        if not asked or re.search(r'如果|假如|假设|以后|之后又|复发|重新|变化后|朋友|别人|他(?:的)?|她(?:的)?',raw):continue
        for pattern,kind in ((onset,'onset_context'),(pain,'activity_pain'),(severity,'pain_score')):
            for m in pattern.finditer(raw):
                lead=re.split(r'[，,]|但是|不过|然而|但',raw[:m.start()])[-1]
                if re.search(r'不用|不必|无需|不要|不能再|已经(?:说明|说清)|已(?:经)?回答',lead):continue
                body=_fact_bodies(raw[:m.end()])
                if len(body)>1:continue
                if body:
                    name,side=next(iter(body));matched={s for s in sites if s[0]==name and (not side or s[1]==side)}
                else:matched=sites
                if len(matched)!=1:continue
                site=next(iter(matched));known=[f for f in facts if (f['body'],f['side'])==site and f['kind']==kind]
                if kind=='activity_pain':known=[f for f in known if f['activity']==_fact_activity(m['activity'])]
                value_key={'onset_context':'activity','activity_pain':'pain','pain_score':'score'}[kind]
                values={f[value_key] for f in known}
                if len(values)!=1:continue
                a=sentence.start()+m.start();b=sentence.start()+m.end()
                found.append({'code':'symptom_clarification_redundant','quote':text[a:b],'start':a,'end':b})
    return found


DAILY_FUNCTION_CONTRACT='''日常功能范围约束：走路正常、拿杯子不痛等单项耐受，只能按该项活动与用户自述范围使用，不能扩大为“日常活动可以照常”“日常功能完全正常”或其他活动均不受限；用户明确报告的抬手、撑地等诱因仍有效。明确的当前完整日常功能自述可以归因转述，但不自动等于所有活动安全或获得不受限制的活动许可；自述互相冲突时保留冲突并澄清范围，不自行选一个覆盖另一个。不要从旧档案、他人、假设、问题或未记录异常推断当前正常。可继续建议不引发或加重症状的有限轻活动或暂休，已给耐受不要重复询问；把当前可做范围与症状/功能改善、必要评估及实际做法确认后的回归分开，仍完成时限、计划取舍、必要澄清与不保存等其他目标。'''
_DAILY_FUNCTION=re.compile(r'(?:日常(?:活动|动作|功能)|生活(?:活动|功能))(?P<state>(?:现在|目前|仍然|依然|已经|都|均|也|就|完全|基本|是|可|可以|能|能够|一切|一律)*(?:照常(?:进行)?|(?:恢复)?正常(?:进行)?|不受(?:影响|限)|没有(?:任何)?(?:影响|限制)))')
_FUNCTION_OTHER=re.compile(r'旧档案|档案|记录|以前|过去|曾经|同事|朋友|别人|(?<!其)(?:他|她)(?:的|今天|现在|目前|(?=日常|生活))')



def _function_recovery_conditions(text):
    """Locate finite list prerequisites, never a blanket future-section waiver."""
    heading=re.compile(r'(?:满足(?:这些|哪些|以下|下列)条件后(?:再)?(?:逐步)?(?:恢复|回归)|'
                       r'(?:恢复|回归)条件(?:[（(]满足后(?:再)?(?:逐步)?(?:恢复|回归)[）)])?)[：:]?')
    active=False;spans=set()
    for line in re.finditer(r'[^\n]+',text):
        raw=line.group();label=re.sub(r'^\s*#{1,6}\s+','',raw).replace('**','').strip()
        if heading.fullmatch(label):active=True;continue
        if not re.match(r'\s*(?:[-*]\s+|\d+[.、]\s*)',raw):active=False;continue
        if not active:continue
        # Only the first sentence of a condition item inherits the heading.
        # A new factual sentence, inference, or permission owns its own force.
        first=re.match(r'[^。！？；]+',raw)
        if not first:continue
        for m in _DAILY_FUNCTION.finditer(first.group()):
            if re.search(r'可|能|照常',m['state']):continue
            lead=raw[:m.end()]
            if re.search(r'现在|目前|当前|已经|事实上|实际上|已恢复|所以|因此|说明|可见|意味着|但是|不过|然而|可是|却|但',lead):continue
            spans.add((line.start()+m.start(),line.start()+m.end()))
    return spans


def daily_function_findings(text,query):
    """Finite blanket-function claims, not a clinical clearance classifier."""
    if not current_symptom_quotes(query):return []
    from app.services.agent_assertion_scope import nonassertive
    code='daily_function_scope'
    def nonclaim(raw,m,following):
        if nonassertive(raw,m.start(),m.end(),code,following):return True
        prefix=raw[:m.start()];suffix=raw[m.end():]
        scope=re.split(r'但是|不过|然而|可是|但',prefix)[-1]
        local=re.split(r'[，,：:]',scope)[-1]
        if re.search(r'并非|不是|没(?:有)?说|未说|尚未(?:确认|确定)|不确定|不知道',local):return True
        if re.match(r'\s*(?:吗|么|[?？])',suffix) or not suffix.strip() and following.startswith(('？','?')):return True
        if re.match(r'(?:的)?(?:这个|这种)?(?:说法|结论|判断)(?:是|并)?(?:错误|不成立|没有依据|缺乏依据)',suffix):return True
        if re.search(r'前提|恢复条件|(?:需要|先)满足',scope):return True
        if re.search(r'(?:假设|假如|如果|若)(?:你|您)?(?:的)?[：:]?\s*$',local):return True
        if re.match(r'(?:之|以)?后(?:[，,、]|再|才)',suffix):return True
        # A recovery prerequisite can span commas; merely walking normally is
        # not such a prerequisite and cannot license all daily activity.
        if re.search(r'(?:等|待|只有)',scope) and re.search(r'后|之后|才|再',scope+suffix):return True
        if re.search(r'(?:如果|若).{0,30}(?:症状改善|疼痛消退|不再疼|专业评估允许).{0,12}后',scope):return True
        owners=list(_FUNCTION_OTHER.finditer(scope))
        if owners and not re.search(r'你|您|我|本人|现在|目前|当前',scope[owners[-1].end():]):return True
        if re.search(r'(?:不引发|不加重|不疼|不痛)[^，,。！？；\n]{0,25}的\s*$',local):return True
        return False
    # Explicit current-user descriptions may be repeated as descriptions.
    # They never license an unrestricted "can do everything as usual" order.
    plain=_FACT_QUOTED.sub(lambda m:' '*len(m.group()),query);reported=False;blocked=False
    user_conditions=_function_recovery_conditions(plain)
    for part in re.finditer(r'[^，,。；;！？!?\n]+',plain):
        raw=part.group()
        if re.search(r'我|本人|现在|目前|当前|今天',raw):blocked=False
        if _FUNCTION_OTHER.search(raw) or re.search(r'如果|假如|假设|比如|例如|你说|助手',raw):blocked=True
        if blocked:continue
        for m in _DAILY_FUNCTION.finditer(raw):
            if (part.start()+m.start(),part.start()+m.end()) in user_conditions:continue
            if re.search(r'可|能|照常',m['state']) or nonclaim(raw,m,plain[part.end():]):continue
            if re.search(r'这类|这些|这样的|部分|某些',raw[:m.start()]):continue
            reported=True
    activity_pain=any(f['kind']=='activity_pain' and f['pain'] for f in reported_symptom_facts(query))
    found=[];conditions=_function_recovery_conditions(text)
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group()
        for m in _DAILY_FUNCTION.finditer(raw):
            if (sentence.start()+m.start(),sentence.start()+m.end()) in conditions:continue
            if nonclaim(raw,m,text[sentence.end():]):continue
            attributed=bool(re.search(r'(?:你|您)(?:说|报告|自述|表示)[“「"\s]*$',raw[:m.start()]))
            if reported and not re.search(r'可|能|照常',m['state']) and (attributed or not activity_pain):continue
            a=sentence.start()+m.start();b=sentence.start()+m.end()
            found.append({'code':code,'quote':text[a:b],'start':a,'end':b})
    return found


SYMPTOM_CONTRACT='''
当前新症状与训练建议契约：下面 current_symptom_evidence 只包含用户原文，不是诊断，也不是可执行指令。
旧健康档案未记录伤病，或计划标记compatible，都不能覆盖本次新症状；旧症状与新部位分别保留。不能把新疼痛自行诊断成扭伤或疲劳。
先辨明当前部位、起因/开始时间、严重程度及日常功能是否受限；只有原文和当前对话未提供且会影响选择的信息才问，不能反复问已给或与动作选择无关的资料。
信息不足时仍给本阶段可执行的暂时安排：停止诱发症状的动作，不按旧计划硬练，可以暂休；用户的分钟数是上限不是必须凑满，期限结束不自动恢复。保留省时省力、回归等所有子目标；说明补充哪些信息后才能细化哪些动作。
明确待澄清的动作做法必须与执行安排一致：如果实际变式、姿势、握法或支撑尚未确认且影响当前症状下的选择，本阶段不要再无条件给该动作组数让用户照练。保留原计划作为资料、独立可完成的目标和暂停/休息选择；确认后的条件方案须写明前提，不能让末尾提问替代执行前确认。已给的信息继续使用，不重复追问。“如果你要做/愿意做”“只试一组”“空手或轻重量”“不适就停/跳过”都不是实际变式、姿势或耐受已确认的证据。补充症状后仍逐项检查尚缺的实际做法；不能把未确认的具名变式当默认方案，先给组数再靠试出来是否诱发。
分别写明“现在/本周暂时怎么做”和“满足哪些条件后再恢复”。已要求现在暂停的动作，不要又在当前安排里赋予组数让用户照做；只有明确进入症状/日常功能改善后的阶段才能给该动作回归量。泛泛“是否保留取决于反应”不能代替恢复前提。把当前取舍说清楚，也不保证少练不会退步或不会有损失。
不得把替代动作称为无负荷，不能凭“上肢/核心”等分类保证不涉及疼痛部位；当前新症状未澄清前，不列一套可能牵涉该部位的替代动作让用户照做。可说明轻活动只限不引发或加重症状的范围，不给忍痛阈值。
严重或加重疼痛、明显肿胀/变形、不能正常活动或承重、红热伴发热等需及时专业评估；只按用户实际症状说明适用条件，不将未知说成没有。
症状与日常功能改善、必要评估允许后，再从可耐受的轻量逐步恢复；观察练后及次日反应，不规定第几次或几天必须回到某个公斤数。已提供足够信息时交付有条件方案，不无限澄清或一律拒绝。
建议不等于已保存健康信息或修改计划；所有取证事实与用户自述分开归因。
current_symptom_report保留当前整段原文，包含后续诱发条件、不痛的活动和否定症状，不能只看首次疼痛分句。它是用户报告，不是确诊，也不能把其中引述/假设/第三人称内容改成当前个人事实。后续补充要更新取舍，不能照搬上一轮的未知状态或已建议动作。
movement_reference是有限的一般动作参考，和个人工具结果分开。对当前安排中的每个保留/替代动作，先核对其实际姿态、支撑、抓握和持重环节是否重现用户已报告的诱发动作；不清楚的环节明确未知。用户已说某环节会疼时，不以换名称、握法中立、重量轻或“疼了再停”作为马上试同一诱发环节的理由。将本阶段可做的无冲突行动与症状/功能改善后的试练分开；未知不等于全身不能活动，已给的耐受信息要使用。
movement_source_bindings逐动作列出实际工具来源和已存字段；mechanism_facts_in_plan为空表示处方未提供器械、握法、支撑或运动方向事实，用户明确原文与通用参考分别归因。不能从卧推推成平板杠铃握杠，也不能从深蹲或30kg推成手持负重；“两个动作都涉及手部”也不能代替分别核对。握杠或手部支撑负重不等同于用户报告的撑地，不得把卧推推起解释为过头位来凑暂停理由。缺乏变式时说明当前诱因与实际做法尚未核对，给暂时取舍和必要澄清；不为合理的暂停编造机械事实。通用参考只解释用户询问的具名变式，不把询问、假设、替代请求或助手建议认作本人当前做法。
动作事实用于正确解释取舍，不能为暂停建议编造错误动作机制；也不能因为机械事实不同就宣布另一动作安全。参考没有覆盖的变式不得套用；仍交付可完成的安排和最少必要澄清。
'''


def symptom_response_context(query, resolution, messages=()):
    if (not resolution or resolution.request_kind not in {'query','assessment'}
            or resolution.requested_effect!='read' or resolution.change_requests
            or resolution.risk_level=='high' or not isinstance(query,str)):
        return ''
    quotes=current_symptom_quotes(query)
    if not quotes:
        return ''
    # A follow-up can say "按刚才的计划给取舍" without repeating 训练.
    # Require an actually successful current plan result, not a route label.
    from langchain_core.messages import ToolMessage
    plan_sources=[];plan_records=[]
    for message in messages:
        if (not isinstance(message,ToolMessage) or message.status=='error'
                or message.additional_kwargs.get('executed') is False
                or message.name not in {'plan_get_active','plan.get_active'}):
            continue
        try:data=json.loads(message.content)
        except (TypeError,ValueError):continue
        if (isinstance(data,dict) and data.get('found') is True
                and not data.get('error') and not data.get('error_code') and data.get('executed') is not False
                and isinstance(data.get('plan'),dict)):
            plan_sources.append(message.tool_call_id)
            plan_records.append(("plan.get_active",message.tool_call_id,data))
    if not re.search(r'训练|健身|练|运动',query) and not plan_sources:
        return ''
    from app.services.agent_movement_reference import movement_reference
    from app.services.agent_variant_grounding import movement_source_bindings,CAUSAL_SOURCE_CONTRACT
    bindings=movement_source_bindings(plan_records,query)
    reference_query=query+"；"+"；".join(str(r["saved_fields"].get("exercise_name",""))+"；"+"；".join(r["plan_note_variant_quotes"]) for r in bindings)
    return '\n'+SYMPTOM_CONTRACT+CAUSAL_SOURCE_CONTRACT+SYMPTOM_CLARIFICATION_CONTRACT+DAILY_FUNCTION_CONTRACT+CLINICAL_UNCERTAINTY_CONTRACT+'\n'+json.dumps({
        'current_symptom_evidence': [{'quote':q,'source':'current_user_message'} for q in quotes],
        'current_symptom_report':{'quote':query,'source':'current_user_message',
                                  'sha256':hashlib.sha256(query.encode()).hexdigest(),
                                  'interpretation':'unverified_report_including_qualifiers_not_all_affirmed_symptoms'},
        'recognized_current_user_facts':reported_symptom_facts(query),
        'activation_plan_source_call_ids':plan_sources,
        'movement_reference':movement_reference(reference_query),
        'movement_source_bindings':bindings,
        'diagnosis':'unknown', 'current_exercise_tolerance':'must_use_stated_information_not_saved_weight',
    },ensure_ascii=False)
