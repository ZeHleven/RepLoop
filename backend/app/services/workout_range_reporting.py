"""Bounded calendar summaries: resolve explicit ranges, verify scope, render facts."""
from dataclasses import dataclass
from datetime import date
import re

from app.services.history_status_scope import effective_history_statuses, parse_history_status_scope, STATUS_LABELS
from app.services.workout_reporting import _n


@dataclass(frozen=True)
class CalendarSummary:
    ranges: tuple[tuple[str, str], ...]
    statuses: tuple[str, ...]
    tools: tuple[str, ...]

    def reads(self):
        return [(tool, {'start_date': start, 'end_date': end, 'statuses': list(self.statuses),
                        **({'limit': 20} if tool == 'workout.list_history' else {})})
                for start, end in self.ranges for tool in self.tools]


_DATE = r'\d{4}-\d{2}-\d{2}'
_RANGE = re.compile(rf'({_DATE})\s*(?:至|到|~|～|—|–)\s*({_DATE})')
# A complete recognized aggregate vocabulary, rather than silently dropping an
# unknown exercise, metric, numeric threshold, or a second task.
_SUMMARY_WORDS = (
    '训练情况', '训练记录', '训练场次', '训练次数', '训练进度', '训练量', '训练时长',
    '训练容量', '负重容量', '总容量', '总时长', '总组数', '总次数', '重复次数',
    '各动作', '每个动作', '动作', '组数', '次数', '时长', '容量', '完成率', '场次',
    '跨场次', '统计', '汇总', '总结', '比较', '对比', '变化', '增减', '情况', '记录',
    '再之前', '之前', '最近', '过去', '当前', '上一', '上一个', '前一个',
    '已完成', '已经完成', '提前结束', '进行中', '正在进行', '跳过', '放弃',
    '全部状态', '所有状态', '其他状态', '其余状态', '不包含', '不包括', '不计入',
    '不统计', '排除', '仅统计', '只统计', '仅看', '只看', '包含', '包括', '状态',
    '用户', '我的', '我', '请', '帮我', '分别', '对应', '之间', '与', '和', '及',
    '以及', '并', '等', '的', '训练', '为', '在', '按', '从', '至', '到', '内', '有',
)
_WORDS = re.compile('|'.join(re.escape(word) for word in sorted(_SUMMARY_WORDS, key=len, reverse=True)))


def calendar_summary(query: str, allowlist: list[str], today: date) -> CalendarSummary | None:
    if 'workout.get_progress' not in allowlist or set(allowlist) - {'workout.get_progress', 'workout.list_history'}:
        return None
    if not re.search(r'总结|汇总|统计|对比|比较', query):
        return None
    matches = list(_RANGE.finditer(query))
    if len(matches) not in (1, 2) or len(re.findall(_DATE, query)) != len(matches) * 2:
        return None
    if len(matches) == 2 and not re.search(r'对比|比较', query):
        return None
    residue = _RANGE.sub('', query)
    residue = re.sub(r'[\d一二三四五六七八九十两]+\s*(?:个)?(?:周|天|日)', '', residue)
    if _WORDS.sub('', residue).strip(' \t\r\n，,。；;、：:（）()【】[]/“”'):
        return None
    status = parse_history_status_scope(query)
    if not status.complete:
        return None
    ranges = []
    try:
        for match in matches:
            start, end = (date.fromisoformat(value) for value in match.groups())
            if not 0 <= (end - start).days < 366 or end > today:
                return None
            ranges.append((start.isoformat(), end.isoformat()))
    except ValueError:
        return None
    ranges.sort()
    if len(ranges) == 2 and ranges[0][1] >= ranges[1][0]:
        return None
    return CalendarSummary(tuple(ranges), effective_history_statuses(status.statuses), tuple(allowlist))


def verify_range_result(tool: str, arguments: dict, result: dict) -> None:
    """A successful call with a different date/status scope is not evidence."""
    if (not isinstance(result, dict)
            or result.get('range_start') != arguments['start_date']
            or result.get('range_end') != arguments['end_date']
            or result.get('status_filter') != arguments['statuses']
            or result.get('timezone') != 'Asia/Shanghai'
            or not isinstance(result.get('as_of'), str)
            or result['as_of'] < arguments['end_date']):
        raise ValueError('calendar_summary_scope_mismatch')
    if tool == 'workout.get_progress':
        if result.get('scope_kind') != 'calendar_range' or result.get('scope_complete') is not True or result.get('truncated') is not False:
            raise ValueError('calendar_summary_incomplete')
    else:
        rows = result.get('sessions')
        total = result.get('total_count')
        if (not isinstance(rows, list) or type(total) is not int or result.get('count') != len(rows)
                or not len(rows) <= total or result.get('truncated') is not (len(rows) < total)
                or any(not arguments['start_date'] <= row.get('trained_at', '') <= arguments['end_date']
                       or row.get('status') not in arguments['statuses'] for row in rows)):
            raise ValueError('calendar_history_incomplete')


def _duration(row: dict) -> str:
    missing = row['duration_missing_sessions']
    return f"已记录时长{_n(row['recorded_duration_min'])}分钟" + (f"（另有{missing}次时长未记录）" if missing else '')


def render_calendar_summary(rows: list[dict]) -> str:
    rows = sorted(rows, key=lambda row: row['range_start'])
    lines = []
    for index, row in enumerate(rows):
        label = ('前一区间' if index == 0 else '后一区间') if len(rows) == 2 else '统计区间'
        lines.append(f"{label}：{row['range_start']}至{row['range_end']}（含首尾日期）。")
        if not row['total_sessions']:
            lines.append('该范围内没有符合筛选条件的训练记录；无记录不等于没有运动。')
            continue
        lines.append(f"共{row['total_sessions']}次训练，{_duration(row)}；"
                     f"{row['total_sets']}组、{row['total_reps']}次重复、{_n(row['total_volume_kg'])}kg负重容量。")
        states = '、'.join(f"{STATUS_LABELS[key]}{value}次" for key, value in row['status_counts'].items() if value)
        lines.append('场次状态：' + states + '。')
        if not row['target_sets_missing_exercises'] and row['recorded_target_sets']:
            ratio = row['total_sets'] / row['recorded_target_sets'] * 100
            lines.append(f"已记录组数/这些记录中的目标组数：{row['total_sets']}/{row['recorded_target_sets']}（{_n(round(ratio, 2))}%）；不代表整个训练计划的完成率。")
        for exercise in row['exercises']:
            lines.append(f"- {exercise['exercise_name']}：{exercise['sets']}组、{exercise['reps']}次重复、{_n(exercise['volume_kg'])}kg负重容量。")
    if len(rows) == 2:
        previous, current = rows
        changes = []
        for key, label, unit in [('total_sessions', '训练场次', '次'), ('total_sets', '组数', '组'),
                                  ('total_reps', '重复次数', '次'), ('total_volume_kg', '负重容量', 'kg')]:
            delta = round(current[key] - previous[key], 1)
            changes.append(f"{label}{'增加' if delta > 0 else '减少'}{_n(abs(delta))}{unit}" if delta else f'{label}持平')
        if not previous['duration_missing_sessions'] and not current['duration_missing_sessions']:
            delta = current['recorded_duration_min'] - previous['recorded_duration_min']
            changes.append(f"记录时长{'增加' if delta > 0 else '减少'}{abs(delta)}分钟" if delta else '记录时长持平')
        lines.append('后一区间相比前一区间：' + '，'.join(changes) + '。')
        old_volume = previous['total_volume_kg']
        if old_volume:
            change = (current['total_volume_kg'] - old_volume) / old_volume * 100
            lines.append(f"负重容量变化{change:+.2f}%（以前一区间为基准）。")
        else:
            lines.append('前一区间负重容量为零，不计算百分比变化。')
    states = '、'.join(STATUS_LABELS[s] for s in rows[0]['status_filter'])
    lines.append(f"仅统计状态为{states}的实际记录；时区Asia/Shanghai，截至{rows[0]['as_of']}。容量由实际记录的重复次数与重量计算，不能据此判断力量、肌肉增长或恢复情况。")
    return '\n\n'.join(lines)
