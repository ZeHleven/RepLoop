"""Render bounded recent-record queries from actual sets, never target weights."""
from collections import Counter
import re

from app.services.workout_queries import normalized_sets, sets_metrics
from app.services.workout_reporting import number, _n


def render_calendar_history(data: dict) -> str:
    """Describe a server-selected calendar range without inventing trends."""
    lines = [f"统计范围：{data['range_start']}至{data['range_end']}；截至{data['as_of']}（Asia/Shanghai）。"]
    total = data['total_count']
    if not total:
        return lines[0] + '\n\n该范围内没有已记录的完成或提前结束训练；无记录不等于没有运动。'
    if data['truncated']:
        lines.append(f"范围内共{total}次训练，以下仅展示最近{data['count']}次；展示记录的组次和容量不代表全范围合计。")
    else:
        rows = data['sessions']
        lines.append(f"共{total}次训练、{sum(row['total_sets'] for row in rows)}组、"
                     f"{sum(row['total_reps'] for row in rows)}次重复、"
                     f"{_n(sum(row['total_volume_kg'] for row in rows))}kg负重容量。")
    for row in sorted(data['sessions'], key=lambda item: item['trained_at']):
        state = '提前结束' if row['status'] == 'ended_early' else '已完成'
        lines.append(f"- {row['trained_at']}（{state}）：{row['total_sets']}组、{row['total_reps']}次重复、{_n(row['total_volume_kg'])}kg负重容量。")
        for exercise in row.get('exercises', []):
            completed = normalized_sets(exercise.get('sets_data'))
            groups = Counter((item.get('weight_kg'), item['reps']) for item in completed
                             if isinstance(item.get('reps'), int) and item['reps'] > 0)
            parts = [f"{_n(weight)}kg×{reps}次×{count}组" if isinstance(weight, (int,float))
                     else f"重量未记录×{reps}次×{count}组" for (weight,reps),count in groups.items()]
            if parts:
                lines.append(f"  {exercise.get('exercise_name') or '未命名动作'}：" + '，'.join(parts) + '。')
    lines.append('这里只描述该范围内实际记录；不能仅凭这些数字判断力量、肌肉增长或恢复情况。')
    return '\n\n'.join(lines)


def history_limit(query: str) -> int | None:
    match = re.search(r'最近\s*([\d一二三四五六七八九十两]+)\s*次', query)
    if not match or not any(word in query for word in ('列出', '列一下', '比较', '对比')):
        return None
    limit = number(match[1])
    return limit if 1 <= limit <= 20 else None


def render_history(data: dict, query: str) -> str | None:
    rows = data.get('sessions', [])
    if not rows:
        return '没有查询到训练记录，无法比较训练变化；无记录不等于没有运动。'
    names = sorted({ex['exercise_name'] for row in rows for ex in row.get('exercises', [])
                    if ex.get('exercise_name') and ex['exercise_name'] in query})
    if not names:
        # Open-ended action or performance questions stay in the general loop.
        if any(word in query for word in ('比较', '对比', '动作', '重量', '强度', '进步')):
            return None
        lines = ['最近训练记录（按日期倒序）：']
        for row in sorted(rows, key=lambda item: item['trained_at'], reverse=True):
            state = '' if row.get('status') == 'completed' else f"（状态：{row.get('status', '未知')}）"
            lines.append(f"- {row['trained_at']}：{row['total_sets']}组，{_n(row['total_volume_kg'])}kg总负重容量{state}。")
        return '\n'.join(lines)
    sections = []
    for name in names:
        lines = [f'{name}的实际完成记录：']
        history = []
        for row in sorted(rows, key=lambda item: item['trained_at']):
            sets = [s for ex in row.get('exercises', []) if ex.get('exercise_name') == name
                    for s in normalized_sets(ex.get('sets_data')) if isinstance(s.get('reps'), (int, float)) and s['reps'] > 0]
            if not sets:
                continue
            count, reps, volume = sets_metrics(sets)
            groups = Counter((s.get('weight_kg'), s['reps']) for s in sets)
            description = '；'.join(f"{_n(weight) + 'kg' if weight is not None else '重量未记录'}×{_n(repetition)}次×{n}组"
                                    for (weight, repetition), n in groups.items())
            lines.append(f"- {row['trained_at']}：{description}；共{count}组、{reps}次重复；{name}容量{_n(volume)}kg。")
            weights = {s['weight_kg'] for s in sets if s.get('weight_kg') is not None}
            history.append((row['trained_at'], count, reps, volume, weights))
        for previous, current in zip(history, history[1:]):
            changes = [f'完成组数{previous[1]}→{current[1]}，重复次数{previous[2]}→{current[2]}']
            if len(previous[4]) == len(current[4]) == 1:
                a, b = next(iter(previous[4])), next(iter(current[4]))
                changes.append(f'实际重量{_n(a)}kg→{_n(b)}kg（变化{_n(b-a)}kg）')
            changes.append(f'动作容量{_n(previous[3])}kg→{_n(current[3])}kg（变化{_n(current[3]-previous[3])}kg）')
            lines.append(f'{previous[0]}至{current[0]}：' + '；'.join(changes) + '。')
        if not history:
            lines.append('这些记录中该动作没有有效完成组，无法比较变化。')
        sections.append('\n'.join(lines))
    sections.append('这里只描述实际记录的变化；不能仅凭这些数字判断力量水平、肌肉增长或恢复情况。')
    return '\n\n'.join(sections)
