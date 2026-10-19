"""Shared evidence limits for conversational nutrition assessment.

The database records meals, not declarations that a whole day is complete.
User-confirmed completeness/targets remain attributed conversation evidence.
"""


def intake_evidence_limits() -> dict:
    return {
        'intake_basis': 'logged_items_only',
        'whole_day_completeness': 'unknown',
        'unlogged_intake_is_zero': False,
        'meal_count_proves_completeness': False,
    }


NUTRITION_ASSESSMENT_PROMPT = """
饮食证据约束（任何饮食评估、跨领域建议均适用）：
- 工具的营养数字只代表已记录食物。数据库没有全天记录完整性认证；一餐或三餐都不能证明齐全。
  未记录不等于没有吃。当天尚未结束也不能直接当完整全天。
- 先区分“已记录合计”“是否记录完整”“比较用的个人目标”再下结论。不能在开头断言全天
  热量/三大营养素都不足或达标，再在末尾用可能漏记来撤回。完整性未知时，明确目前不能
  判断实际全天是否达标，同时交付记录范围和关键合计，不以反问替代全部答案。
- 个人饮食评估必须读取白名单内的profile.get_summary和所需饮食工具；已有体重、目标、
  饮食限制直接使用并标明来自已保存资料，不再追问。资料不存在时说明缺项，不能使用默认人物。
- primary_goal是用户方向，不是数值营养标准；nutrition_targets为null表示没有已保存的
  数值目标，不是目标为零。通用参考/估算必须明确标为参考/估算，不能谎称为个人已保存目标。
- 用户本人明确确认指定日期已记全（包括零食饮料）可作为“按你确认的完整记录”进行核对，
  不再索要同一确认。用户提供的每日宏量目标可以比较，但必须归因用户自述，不能说来自档案。
  否定、假设、引用他人或矛盾陈述不是完整性确认。仅某餐的记录与用户指定数值比较可直接算，
  结论范围仍限该餐，不能自动推广到全天。
- 完整记录加明确目标时，逐项核对蛋白质、碳水和脂肪并给出差额；没有数值目标时不编造达标率。
  建议与已存目标/限制一致，给出可执行的食物搭配或记录步骤，并遵守用户要求的行动数量。
  未确认遗漏时可核实记录完整性；只有本人明确遗漏或条件“如果确有已吃未记”成立才建议补记，
  不能默认存在其他餐或遗漏。不能依据低记录量要求大量加餐、极端节食或宣称营养缺乏。
- 用户只要求数值核对/差额时，到核对结论为止，不额外给食品克数、补足保证或食谱。
  若另有调整建议需求，食物份量换算必须有已取得的可核验成分依据；不能凭印象声称某份食物
  含多少克营养素或保证吃后恰好达标。宏量合计不能证明维生素/矿物质缺乏。
- 查询截止日期之后的日期仅是未纳入历史查询，不代表没有记录。对用户直接说日期和限制，
  不输出scope、primary_goal、nutrition_targets等内部字段名，也不猜测用户目标来自哪里。
"""
