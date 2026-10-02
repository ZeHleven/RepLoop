"""Calendar and arithmetic facts for workout reports; no model-generated numbers."""
from dataclasses import dataclass
from datetime import date, timedelta
import re
from app.services.business_clock import BUSINESS_DATE as REPORT_DATE, SHANGHAI, business_today

METRICS = ('sessions', 'sets', 'reps', 'volume_kg')


def report_today() -> date:
    return business_today()


def enrich_progress(data: dict, today: date) -> dict:
    result = dict(data)
    weekly = []
    for source in data['weekly']:
        row = dict(source)
        start = date.fromisoformat(str(row['week_start']))
        end = start + timedelta(days=6)
        row.update(week_start=start.isoformat(), week_end=end.isoformat(), is_complete=end < today)
        weekly.append(row)
    result.update(as_of=today.isoformat(), timezone='Asia/Shanghai', weekly=weekly)
    result['averages_per_calendar_week'] = {
        metric: round(sum(row[metric] for row in weekly) / len(weekly), 2) if weekly else 0
        for metric in METRICS
    }
    result['average_denominator_weeks'] = len(weekly)
    return result


def number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = dict(zip('一二三四五六七八九', range(1, 10))) | {'两': 2}
    if '十' in value:
        a, b = value.split('十', 1)
        return digits.get(a, 1) * 10 + digits.get(b, 0)
    return digits.get(value, 0)


@dataclass(frozen=True)
class ProgressRequest:
    weeks: int
    start: date
    end: date
    compare: bool = False


def progress_request(query: str, today: date) -> ProgressRequest | None:
    """Support calendar-week aggregates only; leave other scopes to the agent."""
    current = today - timedelta(days=today.weekday())
    text = re.sub(r'今天(?:是|为)?\d{4}年\d{1,2}月\d{1,2}日', '', query)
    explicit = re.findall(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日', text)
    iso = re.findall(r'\d{4}-\d{2}-\d{2}', text)
    dates = []
    try:
        if len(explicit) == 2:
            dates = [date(int(y or today.year), int(m), int(d)) for y, m, d in explicit]
        elif len(iso) == 2:
            dates = [date.fromisoformat(item) for item in iso]
    except ValueError:
        return None
    if dates:
        start, end = dates
        if start.weekday() != 0 or end.weekday() not in (0, 6) or end < start:
            return None
        if end.weekday() == 0:
            end += timedelta(days=6)
        weeks = (current - start).days // 7 + 1
        if not 1 <= weeks <= 52 or start > today or end > current + timedelta(days=6):
            return None
        return ProgressRequest(weeks, start, end, '比较' in query or '对比' in query)
    match = re.search(r'(?:最近|近|过去)([\d一二三四五六七八九十两]+)(?:个)?周', query)
    if match:
        weeks = number(match[1])
        if not 1 <= weeks <= 52:
            return None
        return ProgressRequest(weeks, current - timedelta(weeks=weeks-1), current + timedelta(days=6),
                               any(word in query for word in ('比较', '对比', '趋势')))
    if '上上周' in query:
        return ProgressRequest(3, current - timedelta(weeks=2), current - timedelta(days=8))
    if '上周' in query or '前一周' in query:
        both = any(word in query for word in ('本周', '这周'))
        return ProgressRequest(2, current - timedelta(weeks=1),
                               current + timedelta(days=6) if both else current - timedelta(days=1), both)
    if '本周' in query or '这周' in query:
        return ProgressRequest(1, current, current + timedelta(days=6))
    return None


def _n(value: float) -> str:
    return f'{value:,.2f}'.rstrip('0').rstrip('.') if value % 1 else f'{int(value):,}'


def metric_text(values: dict) -> str:
    return f"{_n(values['sessions'])}次训练、{_n(values['sets'])}组、{_n(values['reps'])}次重复、{_n(values['volume_kg'])}kg负重容量"


def scope_progress(data: dict, request: ProgressRequest) -> dict:
    """One presentation projection for prose and cards; never mutate tool evidence."""
    rows = [row for row in data['weekly'] if request.start <= date.fromisoformat(str(row['week_start'])) <= request.end]
    expected_count = (request.end - request.start).days // 7 + 1
    if len(rows) != expected_count:
        raise ValueError('progress_range_incomplete')
    totals = {key: sum(row[key] for row in rows) for key in METRICS}
    result = dict(data, weekly=rows, weeks=len(rows), range_start=request.start.isoformat(),
                  range_end=request.end.isoformat(), average_denominator_weeks=len(rows))
    result.update({'total_' + key: value for key, value in totals.items()})
    result['averages_per_calendar_week'] = {key: round(value / len(rows), 2) for key, value in totals.items()}
    result['daily'] = [row for row in data.get('daily', [])
                       if request.start.isoformat() <= str(row.get('date', '')) <= request.end.isoformat()]
    if not request.start.isoformat() <= str(data.get('selected_week') or '') <= request.end.isoformat():
        result['selected_week'] = None
    return result


def render_progress(data: dict, request: ProgressRequest, query: str) -> str:
    """Render only supported facts; inference never expands beyond the evidence."""
    data = scope_progress(data, request)
    today = date.fromisoformat(data['as_of'])
    rows = data['weekly']
    totals = {key: data['total_' + key] for key in METRICS}
    lines = [f'统计范围：{request.start.isoformat()}至{request.end.isoformat()}；截至{today.isoformat()}（Asia/Shanghai）。',
             f'合计：{metric_text(totals)}。']
    if not totals['sessions']:
        lines.append('该范围内没有已记录的完成训练，无法据此判断是否进步；无记录不等于没有运动。')
        return '\n\n'.join(lines)
    if len(rows) > 1:
        average = {key: round(value / len(rows), 2) for key, value in totals.items()}
        lines.append(f'按{len(rows)}个日历周计算的周平均：{metric_text(average)}。')
        lines.append('\n'.join(f"- {row['week_start']}至{row['week_end']}：{metric_text(row)}"
                               + ('（未结束周，仅截至统计日）' if not row['is_complete'] else '（完整周）')
                               for row in rows))
    partial = any(not row['is_complete'] for row in rows)
    if partial:
        current = today - timedelta(days=today.weekday())
        lines.append(f'本周尚未结束，周日为{(current + timedelta(days=6)).isoformat()}；'
                     '含本周的周平均是当前日历窗口平均，不代表完整周平均。不能用部分周总量与完整周直接判断退步或持续下降。')
    if request.compare:
        completed = [row for row in rows if row['is_complete']]
        for previous, current in zip(completed, completed[1:]):
            comparisons = []
            for metric, label in [('sessions', '训练次数'), ('volume_kg', '负重容量')]:
                base, value = previous[metric], current[metric]
                if base == 0:
                    comparisons.append(f'{label}由0变为{_n(value)}，基期为0，不计算百分比')
                elif value == base:
                    comparisons.append(f'{label}不变（0%）')
                else:
                    pct = abs((value-base)/base*100)
                    comparisons.append(f"{label}{'增加' if value > base else '下降'}{pct:.1f}%")
            lines.append(f"完整周比较（{current['week_start']}相对{previous['week_start']}）：" + '；'.join(comparisons) + '。')
    if request.compare or any(word in query for word in ('进步', '退步', '强度', '正常', '怎么样', '趋势')):
        lines.append('上述统计描述已记录的次数和容量变化；仅凭这些汇总不能判断力量、肌肉增长、恢复情况或训练强度是否变化。')
    return '\n\n'.join(lines)
