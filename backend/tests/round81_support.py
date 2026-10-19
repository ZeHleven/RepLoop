import copy,json
from pathlib import Path
from langchain_core.messages import ToolMessage
from app.services.agent_intent import IntentResolution

DATA=json.loads((Path(__file__).parent/'fixtures/round81/users.json').read_text(encoding='utf8'))
ROWS={r['id']:r for r in DATA['rows']}

def resolution(id='U01-r1',**changes):
    row=ROWS[id]
    values=dict(primary_intent='nutrition_today_query',intent_domain='nutrition',request_kind=row['body']['request_kind'],requested_effect='read',risk_level='low',confidence=.95,resolved_query=row['message'])
    return IntentResolution(**{**values,**changes})

def messages(id='U01-r1'):
    # Audits deliberately store compact summaries; cards hold original data.
    cards={c['type']:c['data'] for c in ROWS[id]['body']['cards']}
    return [ToolMessage(content=json.dumps(cards.get(c['tool_name'],c['result_data']),ensure_ascii=False),name=c['tool_name'].replace('.','_'),tool_call_id=c['call_id']) for c in ROWS[id]['tool_audits']]

def corrected(id):
    if id.startswith('U03'):
        return ('本周可在符合饮食限制、平时能耐受的前提下，正餐保留主食，搭配一种蛋白质食物和蔬菜。'
                '这是一般搭配方向，不是已确定的个人营养目标；不用为下周提前限制总量。\n\n'
                '外出当天：出发前先安排正常的早餐和之后的用餐时间，带一瓶水，路上按实际饭点选择合适的食物。'
                '范围是出去玩的一天，不能只顾其中一顿；无需为了出游提前挨饿或事后补罚。\n\n'
                '没有保存或修改任何饮食记录。')
    data=next(c['data'] for c in ROWS[id]['body']['cards'] if c['type']=='nutrition.get_recent_context')
    days=data['recent_days']
    text='截至2026-10-09，本次默认查看最近14个有记录日期，实际返回9日：2026-09-24至2026-10-09，不是14个连续自然日。以下只代表保存记录。\n'
    text+='\n'.join(f"{d['date']}：{d['total_calories']}千卡、蛋白质{d['total_protein_g']}g、碳水{d['total_carbs_g']}g、脂肪{d['total_fat_g']}g。" for d in days)
    return text+('\n9天里有6天只记了一餐，另3天有两餐记录；这不能证明实际全天进食是否已记齐。'
                 '蛋白质、碳水、脂肪三项都无法判断个人是否达标：完整摄入未知，本次档案返回没有数值型营养目标，保持体重不等于克数目标。'
                 '\n若符合饮食限制且平时能耐受，下次正餐可搭配一份蔬菜。这是一般饮食结构建议，不表示已判断你缺乏营养或应该减少主食。'
                 '如需精确对照，可再给出目标及对应日期的完整摄入确认。本次没有保存或修改记录。')
