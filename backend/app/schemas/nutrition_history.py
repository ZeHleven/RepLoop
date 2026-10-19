from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MealType = Literal['早餐', '午餐', '晚餐', '加餐']
Metric = Literal['calories', 'protein_g', 'carbs_g', 'fat_g']
Operation = Literal['list', 'sum', 'mean', 'max', 'min', 'range']


class NutritionHistoryArguments(BaseModel):
    model_config = ConfigDict(extra='forbid')

    days: int = Field(default=30, ge=1, le=30, description='仅无日期筛选时生效：最多返回的有记录日期数，不是连续自然日数。')
    start_date: str = Field(default='', description='未指定日历范围时必须为空字符串（不是null或字符串null）。日历区间起点YYYY-MM-DD，含当日，与end_date成对；不与logged_dates混用。')
    end_date: str = Field(default='', description='未指定日历范围时必须为空字符串（不是null或字符串null）。日历区间终点YYYY-MM-DD，含当日；区间最多366天，不受days截断。')
    logged_dates: list[str] = Field(default=[], max_length=30, description='离散日期YYYY-MM-DD；不与日历区间混用。')
    meal_types: list[MealType] = Field(default=[], max_length=4, description='只汇总指定餐次；空数组表示所有餐次。')

    @field_validator('start_date', 'end_date')
    @classmethod
    def check_calendar_date(cls, value):
        if value:
            try:
                valid = date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('Use YYYY-MM-DD or empty string for an unspecified bound')
        return value

    @model_validator(mode='after')
    def check_scope(self):
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError('Both calendar bounds are required')
        if self.start_date and self.logged_dates:
            raise ValueError('Calendar range and discrete dates are exclusive')
        values = self.logged_dates + ([self.start_date, self.end_date] if self.start_date else [])
        for value in values:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError('Use ISO dates')
        if self.start_date and not 0 <= (date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date)).days < 366:
            raise ValueError('Calendar range must span 1 to 366 days')
        if len(self.logged_dates) != len(set(self.logged_dates)) or len(self.meal_types) != len(set(self.meal_types)):
            raise ValueError('Duplicate selections')
        return self


class MacroComparisonTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metric: Literal['protein_g', 'carbs_g', 'fat_g']
    value: float = Field(ge=0, allow_inf_nan=False)
    source_quote: str = Field(min_length=1, max_length=80, description='逐字引用用户原文中的指标、数值与克/g单位，例如蛋白质30克。不能引用模型生成的目标。')


class DateComparison(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metric: Metric
    left_date: str = Field(description='被减数日期YYYY-MM-DD；报告计算left_date的记录量减right_date。')
    right_date: str = Field(description='减数日期YYYY-MM-DD；与left_date不同。')
    left_date_quote: str = Field(min_length=1, max_length=20, description='逐字引用原文日期，例如2026年9月27日、10月2日、昨天。')
    right_date_quote: str = Field(min_length=1, max_length=20, description='逐字引用另一日期；同月省略可引用27日。')
    comparison_quote: str = Field(min_length=1, max_length=240, description='逐字引用原文含比较指标及差值/哪天更多/减去关系的语句；明确相减时包含两日期及减去。')

    @model_validator(mode='after')
    def check_dates(self):
        if (any(date.fromisoformat(v).isoformat() != v for v in (self.left_date, self.right_date))
                or self.left_date == self.right_date):
            raise ValueError('Comparison needs two distinct ISO dates')
        return self


class NutritionHistoryPlan(NutritionHistoryArguments):
    supported: bool = Field(description='能完整表达查询条件和交付方式才为true。日期/餐次汇总、空/未来日期说明支持；食品明细和一般搭配建议使用narrative。不能静默忽略不支持的过滤、比较或写入需求。')
    response_mode: Literal['structured', 'narrative'] = Field(default='structured', description='structured用确定性报告回答纯数字汇总/目标差额；narrative在相同完整日期餐次取证后回答逐食物明细、一般搭配建议或记录完整性局限。不得靠切换模式忽略查询条件，也不把个人需要判断当一般建议。')
    metrics: list[Metric] = Field(min_length=1, max_length=4)
    operations: list[Operation] = Field(min_length=1, max_length=6, description='list逐日，sum合计，mean有记录日期均值，max/min含全部并列，range最高最低差。')
    comparison_targets: list[MacroComparisonTarget] = Field(default=[], max_length=3, description='单日单餐或单日全部餐次的已记录合计与用户明确给定宏量克数比较；每指标一次。不推定个人需求或记录完整性，无此需求为空。')
    comparison_scope: Literal['meal', 'day'] = Field(default='meal', description='meal保留一个餐次筛选；day表示单日全部餐次，meal_types必须为空。无比较时填meal。')
    comparison_scope_quote: str = Field(default='', max_length=120, description='day比较必须逐字引用用户原文的每日/全天目标作用域，例如每日目标；不能据餐次数量猜全天。meal或无比较为空。')
    date_comparisons: list[DateComparison] = Field(default=[], max_length=4, description='两个指定日期的记录量比较，每指标一次；保留逐日list及所有请求指标。没有跨日比较时为空。不用于两个期间或个人达标判断。')

    @model_validator(mode='after')
    def check_comparison(self):
        if self.comparison_targets:
            single_day = (bool(self.start_date) and self.start_date == self.end_date) or len(self.logged_dates) == 1
            target_metrics = [t.metric for t in self.comparison_targets]
            valid_meals = len(self.meal_types) == 1 if self.comparison_scope == 'meal' else not self.meal_types
            if (not single_day or not valid_meals or len(target_metrics) != len(set(target_metrics))
                    or not set(target_metrics) <= set(self.metrics) or not set(self.operations) <= {'list', 'sum'}):
                raise ValueError('Target comparison requires one date, explicit meal/day scope and list/sum operations with all target metrics')
            if self.comparison_scope == 'day' and not self.comparison_scope_quote.strip():
                raise ValueError('Daily target scope requires a verbatim user anchor')
        elif self.comparison_scope != 'meal' or self.comparison_scope_quote:
            raise ValueError('Comparison scope requires comparison targets')
        if self.date_comparisons:
            selected = set(self.logged_dates)
            if self.start_date and date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date) == timedelta(days=1):
                selected = {self.start_date, self.end_date}
            compared = [c.metric for c in self.date_comparisons]
            if (len(selected) != 2 or any({c.left_date, c.right_date} != selected for c in self.date_comparisons)
                    or len(compared) != len(set(compared)) or not set(compared) <= set(self.metrics)
                    or 'list' not in self.operations or self.comparison_targets or self.response_mode != 'structured'):
                raise ValueError('Date comparison needs exactly two selected dates, unique included metrics, list and structured delivery')
        return self
