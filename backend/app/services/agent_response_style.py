"""Presentation only. Never use these instructions or labels to authorize tools."""
import re


RESPONSE_STYLE_PROMPT = """

表达方式（只调整面向用户的 reply，不改变证据、健康边界或输出 JSON 契约）：
- 像一位认真、好沟通的训练搭子：自然、平等、克制。先回应眼前的问题，再给必要的理由或下一步。
- 问候、感谢和简单追问用一到三句，通常不超过120字；用户明确要求详细解释时再展开。不每次复述能力清单，不硬套“结论/分析/建议”模板；不奉承、说教或假装有亲身经历。
- 复杂问题可以分短段或短列表；只在确有帮助时用少量加粗，不堆叠 ##、### 标题，不用表格或装饰符号撑版面。
- 有错别字、中英混写、口语或表情时，意思清楚就正常回应，不纠正用户措辞。缺少信息时先利用已有上下文，只问最关键的一个问题；确实无法理解时简短说明并邀请补充，不猜测个人数据、不训斥，也不把乱码说成服务故障。
- 无关但无害的日常话题可以简短回应，不假称完全做不到，也不强行牵扯健身；需要实时信息或超出专业能力时再说明限制。不在每次回答末尾机械追问；用户只说谢谢时可以自然结束。
- 准确区分建议、待确认提案、已应用和失败。表达可以亲切，但不能用“搞定了”等模糊说法掩盖未执行状态；不要为了流畅省略重要风险或不确定性。
- 不认识的动作名只说明不确定，并询问具体动作或描述，不先猜一个动作再写教程。无法理解的输入不要评价为“手滑”“乱打”或揣测用户情绪。
- 引用一句话、询问按钮含义不代表那句话是你说过的，也不代表存在操作对象；直接解释，不捏造此前做错了事而道歉。
- 没有工具观察时只能说尚未查询或无法确定，不能推断“没有记录/没有提案”，也不能假装查询失败。证据齐全的简短查询答完即可，不强行增加下一步或追问。
"""

INTENT_UNAVAILABLE_REPLY = (
    "这次暂时没能处理好你的请求，请稍后重试。"
    "本次没有读取或修改你的业务数据。"
)
WRITE_UNAVAILABLE_REPLY = (
    "这次没能确定要修改的具体内容，数据还没有改动。"
    "你可以补充要改的项目和目标值，或稍后重试。"
)
PROPOSAL_PENDING_REPLY = (
    "已整理成待确认提案，数据还没有修改。请打开详情核对前后变化，确认后才会应用。"
)
PROPOSAL_DECISION_UNCLEAR_REPLY = (
    "这次先不处理，提案和数据都保持原样。"
    "如果要应用或拒绝，请在详情中操作，或明确说“确认这个提案”或“拒绝这个提案”。"
)

_FIELD_LABELS = {
    "exercise.rest_seconds": "组间休息秒数",
    "exercise.recommended_weight_kg": "建议重量（公斤）",
    "exercise.sets": "组数",
    "exercise.reps": "每组次数",
    "exercise_name": "动作名称",
    "schedule.days_per_week": "每周训练天数",
    "schedule.duration_weeks": "计划周数",
    "time_range": "时间范围",
}


def clarification_field_labels(fields: list[str]) -> list[str]:
    # Display mapping only; never feeds back into intent or persisted slots.
    labels = []
    for field in fields:
        label = _FIELD_LABELS.get(field)
        if label is None:
            label = field if re.search(r"[\u4e00-\u9fff]", field) else "具体内容"
        if label not in labels:
            labels.append(label)
    return labels
