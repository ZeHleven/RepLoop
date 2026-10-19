"""Prepare a complete rewrite without feeding back unsupported draft sentences.

The original draft remains in the delivery receipt. This working input is never
a delivered answer or evidence of task success.
"""
import hashlib
import json
import re
from app.services.agent_assertion_scope import CLINICAL_UNCERTAINTY_CONTRACT, PERSONAL_LOAD_CONTRACT, CURRENT_CARE_CONTRACT

from app.services.agent_variant_grounding import CAUSAL_SOURCE_CONTRACT
from app.services.assessment_grounding import COMPLETION_SCOPE_CONTRACT
from app.services.agent_recovery_bounds import RETURN_LOAD_CONTRACT
from app.services.agent_symptom_context import SYMPTOM_CLARIFICATION_CONTRACT, DAILY_FUNCTION_CONTRACT

MARKER='[此处原句待重新形成：请用实际证据回答，不能复制原结论。]'
REQUIREMENTS={
    'clinical_care_omission':'此项是当前就医行动漏答，锚点句未必为假；不要只删锚点或补未来条件。'+CURRENT_CARE_CONTRACT,
    'daily_function_scope':DAILY_FUNCTION_CONTRACT,
    'training_completion_scope':COMPLETION_SCOPE_CONTRACT,
    'symptom_clarification_redundant':SYMPTOM_CLARIFICATION_CONTRACT,
    'training_variant_unsupported':CAUSAL_SOURCE_CONTRACT+'分别依据movement_source_bindings核对卧推和深蹲等动作的已存字段及用户确认原文。泛称卧推不能推成平板杠铃握杠，泛称深蹲不能推成手持负重；握杠不是撑地，不能把卧推推起解释成过头位附近来凑暂停理由。只给出名称/组次/建议重量时，未说明实际变式与握持方式，不能据此断言必须手持负重、抓握或撑地，也不能反向断言不需要手腕参与、必然不过头。共同指代（如两个动作）和重量也不能补足具体变式事实；动作方向、负重放置方式与个人耐受分别核对。把已存事实与未知机制分开；暂停可以依据当前症状和未知信息解释，但不能编造动作事实。只问尚缺的具体方式；用户已明确说明时使用其原文，不重复追问。不把假设、计划修改请求或举例当已存事实，仍完整交付当前取舍、时间上限、改善后回归及不保存。',
    'training_trigger_unresolved':'用户明确报告当前撑手/握重物会疼，当前具名替代的支撑或握持方式尚未证明避开该诱因。不能用中立握法、轻重量、过程中无痛或疼了再停代替核实，也不能断言该器械必然致痛。先不安排尚未核实的替代；利用用户已经说不痛的活动给当前可执行安排，必要时只问缺失的具体握持/支撑方式，把改善后回归另列。保留时间上限、原计划取舍、必要澄清和不保存；不要编造未指定的深蹲变式或一律停止所有活动。',
    'nutrition_personal_adequacy':'提供已记录的营养数值、实际范围与已给目标的差额；没有个人依据时，不下个人营养已经充足、够用或无需增加的结论。一般参考不替代个人判断。继续交付用户要求的搭配建议。',
    'exercise_load_exemption':PERSONAL_LOAD_CONTRACT,
    'clinical_exclusion':CLINICAL_UNCERTAINTY_CONTRACT,
    'training_effect_guarantee':'不保证减量效果不变或不会退步，也不能仅凭组数/分钟数断言没有训练刺激、达不到刺激、白练或效果为零。效果未知与不能完整覆盖原计划目标不同；按实际时限、诱因和已知恢复信息解释暂时安排，不编造效果结论，也不反向保证一组必然有效。保留取舍、必要澄清与恢复条件，既有处方仍明确归因。',
    'training_action_conflict':RETURN_LOAD_CONTRACT+'同一动作的当前暂停与正组数安排冲突。时间不可行结论也须受证据约束：分开估算假设、实际未知和处方休息下界；没有充分下界不能断言原计划必然装不进用户时限，末尾补“只是估算”不能保留先前确定断言。用有条件估算说明取舍，症状暂停单独说明，不改成保证做得完；继续交付时限、澄清与回归目标。回归次数起点与实际临时处方也必须一致：不要照抄另一分配示例的次数；若已是目标次数就保持，不虚构降低后的起点。明确中间调整后再以新次数续接，区分各动作、次数范围及每周频率；不能只删矛盾数字而丢掉用户要求的回归条件。计时按实际答复采用的组次和已声明节奏/休息逐项复算：动作时间、每组后估算休息、热身收尾分开，准备收尾只加一次；同动作内必要组间休息按各动作(组数−1)计算，不能混用每组后休息估算或多日总和。即便总时限确实不足，引用的休息数字也必须正确；“所以/因此”后的必然结论须有独立下界依据。必要澄清未完与无条件当前组数也冲突：实际变式、姿势、握法或支撑仍待确认时，不能一边问一边让用户照练该动作。同一动作后文省略名称的“只做/试1组”仍是处方；“如果愿意”“轻重量”“不适就跳过”不能代替执行前确认。后续补充症状不等于实际变式已确认，不把通用具名变式直接作为当前试练方案。保留独立可完成目标，说明确认哪些前提后才能细化；来源处方与未来条件方案分开，不重复询问已提供信息。症状出现/复发分支也不能继续安排原力量动作或只减组减重；把纯疲劳减量、出现症状时停止诱发动作并重新确认、改善后的回归分开，不能让“或休息”选项掩盖同时给出的继续训练指令。已恢复用户仍按当前无痛事实处理，不诊断复发。先根据当前已知症状选择一致的暂时行动；已要求暂停的动作不能又在当前安排里让用户照练。未来回归须明确症状/功能改善等前提，不能用泛泛的“取决于反应”消除矛盾。保留时间、必要澄清、可休息、回归和不保存等全部目标。',
}


def repair_input_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def build_repair_input(draft,findings,*,records=()):
    spans=[]
    for sentence in re.finditer(r'[^。！？；\n]+[。！？；]?',draft):
        codes=list(dict.fromkeys(f['code'] for f in findings if sentence.start()<=f['start']<f['end']<=sentence.end()))
        if codes:
            spans.append({'start':sentence.start(),'end':sentence.end(),'codes':codes})
    # Refuse to silently omit any future matcher spanning sentence boundaries.
    if any(draft[f['start']:f['end']]!=f['quote'] or not any(s['start']<=f['start']<f['end']<=s['end'] for s in spans) for f in findings):
        raise ValueError('repair input requires exact, sentence-contained findings')
    parts=[];cursor=0
    for span in spans:
        parts.extend([draft[cursor:span['start']],MARKER]);cursor=span['end']
    parts.append(draft[cursor:])
    codes=list(dict.fromkeys(f['code'] for f in findings))
    requirements=[{'code':code,'instruction':REQUIREMENTS[code]} for code in codes]
    if 'training_action_conflict' in codes and records:
        from app.services.agent_training_constraints import draft_work_time_basis
        basis=draft_work_time_basis(draft,records)
        if basis:
            instruction='草稿组次计算依据不是新建议、实际测量或医学许可；每个片段分别绑定原草稿与已存动作来源。仅在沿用该片段组次及节奏时复用；改了组次或节奏就重新计算，不把其他分支或上下文示例数字套进来。动作时间不含休息、准备、换场。'
            for requirement in requirements:
                if requirement['code']=='training_action_conflict':requirement['instruction']+='\n'+instruction+'\n'+json.dumps({'draft_work_time_basis':basis},ensure_ascii=False)
    return {'version':1,'working_draft':''.join(parts),'excluded_spans':spans,
            'requirements':requirements}
