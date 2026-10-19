"""Narrow unresolved-trigger check, not a diagnosis or exercise safety classifier.

An explicit current wrist trigger plus a named current substitute requiring
unverified grip/support is a reason to rewrite, not proof that it causes pain.
Unknown squat variants and unnamed activities are deliberately not classified.
"""
import re
from app.services.agent_symptom_context import current_symptom_quotes

_QUOTES = re.compile(r'“[^”]*”|「[^」]*」|"[^"\n]*"')
_GRIP = re.compile(r'(?:用力)?(?:抓握|握|提|拿|持)(?:重物|重东西|重的东西)|用力(?:抓握|握持)')
_SUPPORT = re.compile(r'(?:手掌|双手|手腕)?撑(?:地|桌|墙)|手掌支撑')
_CANDIDATES = {
    'grip': re.compile(r'(?:坐姿)?哑铃(?:推举|推胸|卧推)|高脚杯(?:式|深蹲)?|'
        r'(?:坐姿)?器械(?:推胸|推)|弹力带(?:推胸|推举|推)|绳索夹胸'),
    'support': re.compile(r'(?:靠墙或地面|靠墙|地面)(?:的)?推类(?:动作)?|(?:靠墙|跪姿|上斜)?俯卧撑'),
}
_DO = re.compile(r'改(?:成|为)|换成|替换(?:成|为)|(?:可以|可|建议|安排|保留|选择|试试|先做|做)[^。！？；\n]{0,45}$')
_STOP = re.compile(r'(?:不建议|不适合|不要|不能|不宜|不做|不安排|不保留|不选择|暂停|先停|先别做|先不做|跳过|不要照)[^，,。！？；\n]{0,40}$')
_RETURN = re.compile(r'(?:等|待|只有|在).{0,60}?(?:不再疼|不疼|不痛|恢复正常|症状改善|症状好转|疼痛消失|专业评估允许).{0,25}?(?:后|才|再)')


def reported_wrist_triggers(query):
    if not any(re.search(r'手腕|腕部|腕关节',q) for q in current_symptom_quotes(query)):
        return set()
    text=_QUOTES.sub(lambda m:' '*len(m.group()),query)
    triggers=set()
    for clause in re.split(r'[，,。；;！？!?\n]',text):
        if re.search(r'如果|假如|假设|例如|比如|朋友|别人|他(?:的|今天)|她(?:的|今天)|已(?:经)?恢复|是否|会不会|不确定|不知道|没(?:有)?说|未说明|未提到',clause):
            continue
        # The same clause must affirm pain. Negative and normal statements do
        # not create a trigger just because the action word occurs nearby.
        if not re.search(r'疼|痛',clause) or re.search(r'不疼|不痛|无痛|没有.{0,4}(?:疼|痛)|没.{0,2}(?:疼|痛)',clause):
            continue
        for key,pattern in [('grip',_GRIP),('support',_SUPPORT)]:
            if pattern.search(clause):triggers.add(key)
    return triggers


def _reported_adaptation(query,candidate,kind,instruction):
    # Only retain a specifically described user-tolerated variant. This is
    # neither a generic name-based exemption nor medical clearance.
    if not re.search(r'你报告|按你描述|绑带|肘托|前臂',instruction):return False
    avoided=r'(?:不用|无需|不需要)(?:手|双手)?(?:抓握|握持)' if kind=='grip' else r'(?:不用|无需|不需要)(?:手掌|双手|手腕)?(?:撑地|支撑)'
    for sentence in re.split(r'[。！？;；\n]',_QUOTES.sub('',query)):
        if (candidate in sentence and re.search(r'我(?:现在|实际|已经|用|做|确认)',sentence)
                and re.search(avoided,sentence) and re.search(r'不疼|不痛|无痛',sentence)
                and not re.search(r'如果|假如|假设|是否|会不会|朋友|别人|不确定|不知道',sentence)):
            return True
    return False


def _declined_topic_heading(text, line, heading):
    # A topic label can quote the user's proposed substitution. Bind it only
    # to an explicit rejection in the immediately following paragraph, never
    # to a distant stop or to a rejection of a different action.
    if not re.match(r'关于(?:把)?.*(?:换成|改成|改为|替换)',heading):return False
    directive=re.compile(r'可以|建议|安排|保留|选择|试试|先做|先练|照做|(?:现在|今天|本周|这周).{0,6}(?:做|练|换|改)|[0-9一二两三四五六七八九十]+\s*(?:组|次)')
    if directive.search(heading):return False
    following=text[line.end():].lstrip('\r\n \t')
    paragraph=re.split(r'\n\s*\n|\n\s*(?:#{1,6}\s|\*\*)',following,maxsplit=1)[0]
    if not re.match(r'(?:我)?(?:(?:不建议|不支持|不要|不能|不宜)这样(?:换|改|替换)|(?:不建议|不支持)(?:[。！？]|$))',paragraph):return False
    # A rejection must not hide "but do one set" or a later current directive
    # whose subject is omitted. Explicit named directives are checked below too.
    for clause in re.split(r'[，,。！？；\n]|但是|不过|然而|但',paragraph):
        if not directive.search(clause) or _STOP.search(clause):continue
        returning=_RETURN.search(clause)
        if returning and not re.search(r'但|不过|然而|现在|今天|当前',clause[returning.end():]):continue
        return False
    return True


def trigger_consistency_findings(text,query):
    triggers=reported_wrist_triggers(query)
    if not triggers:return []
    findings=[];phase='current';source=False;positive_section=False
    for line in re.finditer(r'[^\n]+',text):
        rawline=line.group();heading=rawline.replace('*','').strip().strip('# -0123456789.、 ')
        is_heading=bool(re.match(r'\s*(?:#{1,6}\s|\*\*)',rawline) or re.fullmatch(r'[^。！？；]{1,35}[：:]',heading))
        if is_heading:
            source=bool(re.match(r'(?:已保存|已存|原|当前生效)(?:的)?(?:计划|资料)',heading))
            phase='future' if re.search(r'回归|恢复条件|恢复的时机',heading) else 'stop' if re.match(r'不做|暂停|暂时暂停|避免',heading) else 'current'
            positive_section=bool(re.match(r'可以做|当前安排|临时安排|本周安排',heading))
            if _declined_topic_heading(text,line,heading):continue
        for sentence in re.finditer(r'[^。！？；]+',rawline):
            raw=sentence.group();offset=line.start()+sentence.start()
            # An explicit current instruction cannot hide inside a source or
            # recovery section. A future prerequisite must precede the action.
            current=re.search(r'现在|今天|这周|本周|当前',raw)
            for kind in triggers:
                for match in _CANDIDATES[kind].finditer(raw):
                    before=raw[:match.start()];after=raw[match.end():]
                    clause=re.split(r'[，,]|但是|不过|然而|而|但',before)[-1]
                    if _STOP.search(clause) or re.search(r'不用(?:再|去|做|练)?\s*$',clause):continue
                    if re.match(r'(?:以外|之外)的',after):continue
                    if re.match(r'(?:也|仍然|仍)?解决不了(?:问题)?',after):continue
                    quoted=next((q for q in _QUOTES.finditer(raw) if q.start()<match.start()<match.end()<q.end()),None)
                    if quoted and re.match(r'(?:这个|这种)?(?:做法|说法|安排|建议)?(?:是|并)?(?:不合适|错误|不成立|没有依据|不建议)',raw[quoted.end():]):continue
                    if re.match(r'(?:[”」"])?(?:这个|这种)?(?:做法|说法|安排|建议|替换|替代)?(?:我)?(?:是|并|也)?(?:不合适|错误|不成立|没有依据|不建议|先停|暂停|不做)',after):continue
                    if re.search(r'错误示例|反例|不要照此|不能这样|假设有人',before):continue
                    returning=_RETURN.search(before)
                    if returning and not re.search(r'但|不过|然而|现在|当前',before[returning.end():]):continue
                    if phase in {'future','stop'} and not (current and _DO.search(before)):continue
                    if (source or re.search(r'原计划|已存计划|已保存计划|保存的计划|工具显示',before)) and not (current and _DO.search(before)):continue
                    # An earlier rejected substitution does not govern a later definition.
                    # A colon still governs an explicit list of substitutes.
                    instruction_prefix=re.sub(r'[（(][^）)\n]*[）)]','',before)
                    instruction_clause=re.split(r'[，,—]|但是|不过|然而|而|但',instruction_prefix)[-1]
                    prescribed=bool(_DO.search(instruction_clause) or re.match(r'\s*[：:]?\s*(?:做)?\s*[1-9一二两三四五六七八九]\s*(?:组|次)',after) or positive_section)
                    if not prescribed:continue
                    if _reported_adaptation(query,match.group(),kind,raw):continue
                    start,end=offset+match.start(),offset+match.end()
                    findings.append(dict(code='training_trigger_unresolved',quote=text[start:end],start=start,end=end))
    unique={(f['start'],f['end']):f for f in findings}
    return [unique[key] for key in sorted(unique)]
