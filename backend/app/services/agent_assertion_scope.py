"""Finite assertion grammar, not a semantic or clinical safety classifier.

Separate a predicate's force (assertion, denial, question, goal, selection)
from its wording. Never grant safety based on an exercise name. Original text
and offsets are retained by the caller; an unrecognized phrase is not verified.
"""
import re

BODY = r'(?:膝(?:盖|部|关节)?|脚?踝(?:关节)?|脚|腿(?:部)?|肩(?:部|关节)?|手?腕(?:部|关节)?|肘(?:部|关节)?|腰|背|颈|关节|疼痛部位|不适部位)'
# Negative effect certainty has the same evidence burden as an effect guarantee.
EFFECT_ABSENCE = re.compile(
    r'(?:(?:达不到|得不到|没有|毫无|不会产生|无法产生)(?:任何|有效|足够)?(?:训练|肌肉)?刺激'
    r'|(?:没有|毫无)(?:任何)?训练效果|训练效果(?:就是|为|是)零'
    r'|(?:\d+|[一二两三四五六七八九十])\s*组(?:训练)?(?:也|是|完全)*(?:白练|没用))'
)
EXEMPTION = re.compile(
    r'(?:不(?:会)?(?:直接)?(?:涉及|牵涉|影响|用到|刺激|压到|压|负重)[^，,。！？；\n]{0,10}?' + BODY +
    r'|不(?:直接)?踩地'
    r'|(?:对|给)' + BODY + r'[^，,。！？；\n]{0,8}(?:没有|无|零)(?:负荷|负担|压力)'
    r'|' + BODY + r'(?:已经|已|本身|通常|一般|完全|基本|并|也|都|就|是)*(?:不|无需)(?:会|需要|用|直接|主动|再)*(?:发力|受力|负重|承重)'
    r'|不(?:会)?(?:直接)?(?:需要|靠|用)' + BODY + r'(?:来|去)?(?:发力|受力|负重|承重))'
)
PERSONAL_LOAD_CONTRACT='''个人负荷判断须有实际做法与个人信息依据：动作名称、上肢/下肢分类、档案重量和旧筛查，既不能证明某部位不受力，也不能证明参与或负荷相对更少。同样，不用“通常/一般需要某部位承重、支撑或发力”代替未知实际做法，或据此断定该动作对用户风险更明确；已明确的用户实际做法或自述负荷事实可按原范围保留，但不自动证明安全。未知做法也不能改说撑地或握重物是原动作“最可能涉及/用到的环节”，或用紧邻的“它需要手部握持负重”补机制；高概率措辞同样需要实际依据。暂停理由可说明当前症状和适配尚未核实，不必编造机制。不要写“深蹲手腕参与相对少”“卧推本身不直接压膝盖”再在后面补“仍需确认”；未知前提不能支持这种判断，也不能据此排定个人回归顺序。同样，不能仅凭动作名称说它与某部位“关系更直接/最密切”或影响更大，这仍是需要依据的个人比较；明确自述只按用户报告归因转述，不当成已经证实的动作机制。分别说明已知做法、用户自述和未确认环节，先核对影响选择的实际变式及耐受；有条件的回归以已确认且耐受的做法为起点。不要把这一纠正变成一律不能活动、全部拒绝或重复询问已给信息，继续完成当前取舍、时限和回归条件。'''
# A symptom report is not evidence for ruling out urgent care.
CLINICAL_UNCERTAINTY_CONTRACT='''部分自述、低痛分、没有肿胀麻木或旧筛查未记录伤病，都不能支持排除急症/红旗，也不能据此认定“不属于需要立刻急诊的表现”或“不需要/无需急诊、急救、紧急就医”。不要用“看起来不需要”替代确定排除。否定症状只沿用当前用户明确报告的范围；没有肿胀麻木不能扩成整组变形、活动受限、持续加重等都不存在，也不能在就医警示清单后写“你目前没有这些表现”。只有用户已明确否定清单内全部症状时才可按原范围转述，不能从旧档案、他人、引述、假设或问题中取得否定证据。区分用户报告的事实、当前仍未知的信息与一般条件提醒；有条件的及时评估提示不能撤销前文的医疗排除结论。说明现有资料不足以作这类判断，继续完成可支持的临时取舍、用户时限、必要澄清和改善后回归，不诊断、不承诺安全，也不因为未知就自行断言必须急诊。'''
_TRIAGE_KIND = r'(?:急症|急诊|急救|(?:立即|立刻|马上|紧急)(?:去|到)?(?:医院)?(?:就医|医疗评估)|红旗(?:表现|症状|信号))(?:的)?(?:情况|表现|症状)?'
_TRIAGE_LINK = r'(?:(?!但是|不过|然而|可是|却|但)[^，,。！？；\n])'
TRIAGE = re.compile(
    r'(?:(?:不属于|不是|并非)'+_TRIAGE_LINK+r'{0,24}'+_TRIAGE_KIND+
    r'|(?:已经|已|可以|能够|能)?排除'+_TRIAGE_LINK+r'{0,18}'+_TRIAGE_KIND+
    r'|(?:不需要|无需|不用)(?:立即|立刻|马上|紧急)?(?:去|到|前往)?(?:医院)?(?:急诊|急救|就医))'
)
NEGATION = re.compile(
    r'(?:不能|无法|没法|没办法|没有办法|不应|不宜|不要|不敢|未能|尚不能|不代表|不等于|不足以|没有依据|'
    r'错误(?:地)?|误称|是否|能否|不(?:认为|认定|声称|断言)|不(?:替你|为你)?(?:下|作出|做出|保证|打包票))[^，,。！？；\n]{0,60}$'
)
QUOTES = re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')



# Finite list-anaphor source check. An unrecognized symptom is not certified;
# exact reported negatives remain usable, without deciding clinical urgency.
_WARNING_ATOM = re.compile(r'明显肿胀|肿胀|变形|不能正常活动(?:或承重)?|不能(?:正常)?承重|疼痛持续加重|麻木|红热伴发热')
_WARNING_ABSENCE = re.compile(r'(?:你|您)?(?:目前|现在|当前)?(?:都|也|并)?(?:没有|没出现|未出现)(?:这些|上述|以上)(?:表现|症状|情况|信号)')
_WARNING_SITE = re.compile(r'(左|右|双)?(手腕|腕部|腕关节|肩膀|肩部|肩|膝盖|膝|脚踝|踝部|手)')


def _reported_warning_absences(query):
    # Only literal negative lists in current self-report clauses. A positive,
    # a chart's missing record, or a hypothetical cannot license an absence.
    plain=QUOTES.sub(lambda m:' '*len(m.group()),query)
    absent=set();sites=set();blocked=False
    for part in re.finditer(r'[^，,。；;！？!?\n]+',plain):
        raw=part.group()
        for clause in re.split(r'但是|不过|然而|可是|但',raw):
            if re.search(r'我(?:现在|目前|今天)|本人|现在|目前|今天',clause) or re.match(r'\s*我(?:没有|无|未出现|没出现)',clause):blocked=False
            if re.search(r'旧|档案|记录|病历|以前|过去|上周|昨天|如果|假如|假设|比如|举例|同事|朋友|别人|他|她',clause):blocked=True
            if blocked or re.search(r'是否|是不是|不确定|不知道|尚未确认|不能确认|吗|么',clause):continue
            for site in _WARNING_SITE.finditer(clause):
                name={'腕部':'手腕','腕关节':'手腕','肩膀':'肩','肩部':'肩','膝盖':'膝','踝部':'脚踝'}.get(site[2],site[2])
                sites.add((site[1] or '',name))
            for denial in re.finditer(r'没有|未出现|没出现|无',clause):
                prefix=clause[:denial.start()]
                if re.search(r'不能说|没(?:有)?说|并非|不是|不能确认|是否|不确定',prefix):continue
                tail=clause[denial.end():];pos=0;group=set()
                while True:
                    atom=_WARNING_ATOM.match(tail,pos)
                    if not atom:break
                    group.add(atom.group());pos=atom.end()
                    join=re.match(r'(?:[、/]|和|或|及|以及)(?:没有|无|未出现|没出现)?',tail[pos:])
                    if not join:break
                    pos+=join.end()
                if group and not nonassertive(clause,denial.start(),denial.end()+pos,'clinical_exclusion',plain[part.end():]):absent.update(group)
    # Do not pool negatives across different sides/sites to excuse a blanket
    # "you have none" claim. An explicit scoped restatement remains available.
    specific={name for side,name in sites if side}
    sites={(side,name) for side,name in sites if side or name not in specific}
    return absent if len(sites)<=1 else set()


def clinical_absence_findings(text,query):
    if not isinstance(query,str):return []
    known=_reported_warning_absences(query)
    # Absence of all swelling entails absence of obvious swelling, not vice versa.
    if '肿胀' in known:known.add('明显肿胀')
    found=[]
    for sentence in re.finditer(r'[^。！？；\n]+',text):
        raw=sentence.group()
        for claim in _WARNING_ABSENCE.finditer(raw):
            if nonassertive(raw,claim.start(),claim.end(),'clinical_exclusion',text[sentence.end():]):continue
            prefix=re.split(r'[，,：:—]|但是|不过|然而|可是|但',raw[:claim.start()])[-1]
            if re.search(r'如果|假如|假设|若|待|以前|过去|同事|朋友|别人|他|她',prefix):continue
            if re.search(r'(?:不是|并非)(?:说)?(?:你|您)?(?:目前|现在)?$',prefix):continue
            # Resolve only the local preceding warning list; never scan another
            # paragraph or borrow the source user's list as an answer antecedent.
            start=sentence.start()+claim.start();end=sentence.start()+claim.end()
            paragraph=text.rfind('\n\n',0,start)
            prior=text[max(paragraph+2 if paragraph>=0 else 0,start-350):start]
            required={m.group() for m in _WARNING_ATOM.finditer(prior)}
            if required and not required<=known:
                found.append({'code':'clinical_exclusion','quote':text[start:end],'start':start,'end':end})
    return found

def _active_clause(prefix):
    return re.split(r'[，,]|但是|不过|然而|可是|却|但', prefix)[-1]


def _quoted_denial(sentence, start, end):
    for q in QUOTES.finditer(sentence):
        if not q.start() < start <= end < q.end():
            continue
        before = _active_clause(sentence[:q.start()])
        after = sentence[q.end():]
        # Negated speech governs a coordinated quoted object, not a separate
        # clause. Merely putting an assertion in quotation marks is no waiver.
        if re.search(r'(?:不|不能|不应|不会)(?:把|将)[^，,。！？；\n]{0,120}(?:说成|称为|当作|视为)[^，,。！？；\n]*$', before):
            return True
        if NEGATION.search(before):
            return True
        if re.match(r'(?:这个|这种)?(?:说法|结论|断言)?(?:是|并)?(?:错误|不成立|不正确|不可信|没有依据|缺乏依据)', after):
            return True
    return False


def _is_goal(prefix):
    prefix = _active_clause(prefix)
    goal = re.search(r'(?:目标|目的|希望|要求|原则)(?:是|为|：|:)([^，,。！？；\n]*)$', prefix)
    if not goal:
        return False
    # A stated aim is not a claim of achievement, ability or guarantee. New
    # clauses and finite certainty predicates must still be checked.
    return not re.search(r'已经|已做到|能做到|能够|可以保证|确保|保证|实际上|事实上|本身|属于', goal[1])


def _selects_category(prefix, suffix):
    clause = re.split(r'[，,：:—]', prefix)[-1].strip().strip('*- ')
    selector = re.fullmatch(
        r'(?:(?:剩下的时间|其余时间|这一周|本周|这次|今天|当下))?'
        r'(?:(?:你|您)?(?:就|也|可以|可|建议|先|暂时|优先|仅|只|再|不妨))*'
        r'(?:(?:(?:散步|走路)(?:或者|或))?(?:选择|挑选|恢复|做|保留|尝试|试试|试)(?:那些|这些|一些|部分|些)?(?:完全)?|(?:比如|例如)(?:(?:走路|散步|走一走|走走)[、和及])?)', clause)
    category = re.match(r'[^，,。！？；\n]{0,24}的(?:上肢|下肢)?(?:轻松|轻量|轻)?(?:动作|部分|活动)', suffix)
    if selector and category:
        # An unnamed selection cannot waive a named example introduced next.
        # Retain the existing limited walking illustrations only.
        if re.match(r'[，,]\s*(?:比如|例如)(?!走路|散步|走一走|走走)',suffix[category.end():]):return False
        return True
    # The requested unnamed category is not a proven property of an exercise.
    limited_category = re.fullmatch(
        r'(?:[-* ]*)(?:只|仅)做不引发(?:或|也不|、不)加重[^。！？；\n]{0,25}的轻活动[，,]'
        r'\s*(?:比如|例如)(?:坐姿或卧姿下[、，,])?', prefix)
    if limited_category and re.match(r'的(?:上肢|下肢)?(?:轻)?活动', suffix):
        return True
    # In a symptom-limited activity selection, an illustrative "walk a little"
    # is still a requested category. Do not exempt arbitrary named exercises.
    limited = re.search(r'(?:只|仅)做(?:完全)?不引发[^。！？；\n]{0,55}不加重[^。！？；\n]{0,25}轻活动[，,]\s*(?:比如|例如)$', prefix)
    return bool(limited and re.match(r'的(?:走一走|走走|散步|轻活动)(?:[、，,；。]|$)', suffix))


def _standalone_restriction(sentence, following):
    # Only a body-only imperative bullet with immediate withholding of named
    # movements. A distant disclaimer or heading cannot waive an assertion.
    return bool(re.fullmatch(r'\s*[-*]\s*'+BODY+r'(?:完全)?不(?:负重|受力|支撑|扭转)(?:、不(?:负重|受力|支撑|扭转))*',sentence)
                and re.match(r'。具体动作我不(?:替你)?指定',following))


def nonassertive(sentence, start, end, code, following=''):
    """Whether this located predicate is explicitly nonassertive in its scope."""
    prefix, predicate, suffix = sentence[:start], sentence[start:end], sentence[end:]
    if code == 'nutrition_personal_adequacy' and re.search(r'(?:够不|充不|足不)$',prefix):
        return True  # A-not-A question; never governs a later clause.
    if code in {'training_effect_guarantee','exercise_load_exemption'} and predicate.startswith('不会') and prefix.endswith('会'):
        return True  # A-not-A asks a question; any later guarantee is checked separately.
    if NEGATION.search(_active_clause(prefix)) or _quoted_denial(sentence, start, end):
        return True
    # EFFECT locates 保证 itself: the negator may directly precede the span.
    if predicate.startswith('保证') and re.search(r'(?:不|未|没|没法|无法|不能)(?:会|能|敢|可|愿意)?$', prefix):
        return True
    if code == 'training_effect_guarantee' and re.search(r'(?:不|未|没有)(?:会|能|再)?承诺(?:你|您)?$',_active_clause(prefix)):
        return True
    if code == 'training_effect_guarantee' and EFFECT_ABSENCE.fullmatch(predicate):
        local=_active_clause(prefix)
        if re.search(r'可能|也许|未必|不一定|担心|怀疑|无法确定|没法判断|尚不能确定|(?:不是|并非)[^，,]{0,12}$',local):
            return True
        if re.match(r'\s*(?:吗|么)',suffix) or not suffix.strip() and following.startswith(('？','?')):
            return True
        # "对手腕没有刺激" is a body-load claim, not proof of no training
        # stimulus. Other checks may assess it; this scope grants no safety.
        if re.search(r'(?:对|给)'+BODY+r'[^，,]{0,8}$',local):
            return True
    if code == 'exercise_load_exemption':
        # A denied guarantee or an unresolved category does not establish an
        # exercise property. A later causal/contrast clause owns its assertion.
        local=re.split(r'[，,：:]|但是|不过|然而|可是|却|但|所以|因此|实际上|事实上',prefix)[-1]
        if re.search(r'(?:不|不能|无法)(?:预先|事先|提前)保证[^，,。！？；\n]{0,40}$',local):
            return True
        if re.search(r'(?:尚未|还未|未|还没(?:有)?|没(?:有)?)(?:确认|确定|弄清)(?:哪些|哪个|哪种)(?:动作|活动|部分)(?:一定|完全|会)?$',local):
            return True
    if code == 'clinical_exclusion':
        # A medical-document object is not a decision about seeking care.
        if re.match(r'(?:就诊)?(?:记录|病历)(?:$|[，,：:]|来|作|中|里)',suffix):
            return True
        local=_active_clause(prefix)
        if re.match(r'(?:不是|并非)(?:(?:说|说明|表示|意味着)(?:你|您)?(?:现在|目前)?)?(?:不需要|无需|不用)',predicate):
            return True  # A denial of an exclusion is not an exclusion.
        if re.search(r'是不是|需不需要|能不能|可不可以$',local):
            return True
        if re.fullmatch(r'\s*(?:吗|么|呢)?\s*',suffix) and following.startswith(('？','?')):
            return True  # A terminal question cannot exempt an earlier clause.
        if re.match(r'(?:的)?(?:这个|这种)?(?:说法|结论|判断)(?:是|并)?(?:错误|不成立|不正确|没有依据|缺乏依据)',suffix):
            return True
        for quote in QUOTES.finditer(sentence):
            if quote.start()<start<=end<quote.end() and re.search(r'(?:不|不会|不能)(?:作出|做出|给出|承诺|保证)[^，,。！？；\n]{0,20}$',_active_clause(sentence[:quote.start()])):
                return True
        if re.search(r'(?:尚未|并未|未|没有|没能|未能)(?:完全)?$', prefix):
            return True
        # A future professional assessment is a prerequisite, not a claimed
        # result. "If little pain, then we can exclude" has no such evidence.
        pending = re.search(r'(?:待|等待|等|若|如果|只有)(?:经|经过)?[^，,。！？；\n]{0,24}(?:医生|专业|医疗)[^，,。！？；\n]{0,18}(?:评估|检查|诊断)[^，,。！？；\n]{0,8}$', prefix)
        if pending and re.match(r'(?:后|之后|以后|[，,](?:再|才|然后))', suffix):
            return True
    if code in {'exercise_load_exemption', 'training_effect_guarantee'} and not predicate.startswith('保证') and _is_goal(prefix):
        return True
    if code == 'exercise_load_exemption' and (_selects_category(prefix, suffix) or _standalone_restriction(sentence, following)):
        return True
    return False


# This finite obligation is source-attributed triage support, not a diagnosis.
CURRENT_CARE_CONTRACT = '''当前就医行动不能被未来警示代替：用户明确报告新近受伤，且已有明显疼痛/肿胀或活动、承重困难等需要及时专业评估的表现时，说明现在应及时联系医疗机构评估，不能仅说等加重、不能走路或观察几天不改善才就医。先停止诱发或加重症状的训练，不安排试练来判断伤情。已有行动可再补未来恶化警示；旧伤已恢复、他人、假设、否定或尚未确认的症状不能当本人当前需要。未触发有限规则不等于不需就医；不诊断、不据此一律要求急救。保留原请求中证据支持的其他目标和必要澄清。'''
CLINICAL_UNCERTAINTY_CONTRACT += CURRENT_CARE_CONTRACT
CURRENT_CARE_REPLY = ('你报告的新近受伤和当前症状需要及时由医疗专业人员评估。'
    '请现在联系可就诊的医疗机构安排评估，不要等到症状加重后才求助。'
    '先停止诱发或加重症状的训练；当前资料不能确定损伤程度或是否能恢复训练。\n\n')


def current_care_basis(query):
    """Finite same-episode, current self-report quotes; unknown is not absence."""
    if not isinstance(query,str):return []
    plain=QUOTES.sub(lambda m:' '*len(m.group()),query);found=[]
    injury=re.compile(r'摔倒|跌倒|摔伤|扭伤|扭到|崴|撞到|砸伤|砸到|挤伤|拉伤|碰伤|受伤')
    warning=re.compile(r'(?:明显|严重|大量)(?:的)?(?:肿胀|肿痛|淤青|瘀青)|肿得(?:明显|厉害)|'
                       r'(?:疼|痛)得厉害|(?:剧烈|严重|明显)(?:疼痛|疼|痛)|'
                       r'(?:活动|转动|弯曲|伸直|承重|走路|握杯)(?:都|很|明显)?(?:困难|受限|疼痛|疼)|'
                       r'不能(?:正常)?(?:活动|承重|走路)')
    for episode in re.finditer(r'[^。；;！？!?\n]+',plain):
        raw=episode.group()
        if not re.search(r'今天|今日|刚才|刚刚|现在|目前|这两天',raw):continue
        # Deliberately do not pool multiple people, old records or hypotheticals.
        if re.search(r'如果|假如|假设|比如|例如|朋友|同事|别人|他|她|档案|记录|病历|'
                     r'明天|后天|以前|过去|曾经|去年|前年|[一二三四五六七八九十\d]+年前|上周|'
                     r'已(?:经)?恢复|已经好了|已痊愈',raw):continue
        canonical={'腕部':'手腕','腕关节':'手腕','膝盖':'膝','踝部':'脚踝'}
        sites={(m[1] or '',canonical.get(m[2],m[2])) for m in re.finditer(r'(左|右|双)?(手腕|腕部|腕关节|膝盖|膝|脚踝|踝部|手指|手背|手掌|手心|脚背|脚掌|脚底|脚跟|脚趾|手|肘|肩|腰|小腿|脚|足)',raw)}
        located={body for side,body in sites if side}
        sites={(side,body) for side,body in sites if side or body not in located}
        # A broad hand/foot reference can be refined by one concrete site in
        # the same self-reported episode. It cannot merge opposite sides or
        # two named sites (e.g. fingers versus wrist, foot dorsum versus ankle).
        broad = {'手': {'手腕', '手指', '手背', '手掌', '手心'},
                 '脚': {'脚踝', '脚背', '脚掌', '脚底', '脚跟', '脚趾'},
                 '足': {'脚踝', '脚背', '脚掌', '脚底', '脚跟', '脚趾'}}
        specific = {(side, body) for side, body in sites if body not in broad}
        sides = {side for side, body in sites if side}
        if len(specific) == 1 and len(sides) <= 1:
            refined = next(iter(specific))[1]
            sites = {(side, body) for side, body in sites
                     if refined not in broad.get(body, set())}
        if len(sites)>1:continue
        events=[];warnings=[]
        for clause in re.finditer(r'[^，,]+',raw):
            part=clause.group()
            if re.search(r'是否|是不是|会不会|不确定|不知道|不清楚|尚未确认|吗|么',part):continue
            for kind,pattern,items in [('injury',injury,events),('warning',warning,warnings)]:
                for m in pattern.finditer(part):
                    if re.search(r'(?:没有|没|无|未|不|不是|并非)[^，,]{0,5}$',part[:m.start()]):continue
                    items.append(m.group())
        if events and warnings:
            found.append({'source':'current_user_message','quote':query[episode.start():episode.end()],
                          'start':episode.start(),'end':episode.end(),
                          'reported_injury':events,'reported_warning':warnings})
    return found


def clinical_care_findings(text,query):
    """An omission anchor is not a claim that its quoted sentence is false."""
    if not current_care_basis(query):return []
    plain=QUOTES.sub(lambda m:' '*len(m.group()),text)
    medical=re.compile(r'就医|就诊|(?:到|去)医院|医疗专业人员|医疗机构|医生')
    for part in re.finditer(r'[^。；;！？!?\n]+',plain):
        raw=part.group()
        for m in medical.finditer(raw):
            prefix=re.split(r'但是|不过|然而|可是|但',raw[:m.start()])[-1]
            if not re.search(r'立即|现在|及时|尽快|今天|当日',prefix):continue
            if re.search(r'如果|假如|若|一旦|等到|之后|以后|再|才|是否|不确定|不知道|'
                         r'不需要|无需|不用|不必|不建议|不要|不能|并非|不是|(?:加重|恶化|不改善|不好转)(?:时|后)',prefix):continue
            if re.search(r'吗|么',raw[m.end():]) or plain[part.end():].startswith(('？','?')):continue
            return []
    anchor=re.search(r'[^。！？；\n]+',text)
    if not anchor:return []
    return [{'code':'clinical_care_omission','kind':'omission','quote':anchor.group(),
             'start':anchor.start(),'end':anchor.end()}]
