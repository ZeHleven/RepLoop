"""Deterministic checks complement, but do not replace, semantic review."""
import re
from datetime import date, timedelta


def check_answer(answer: str, contract: dict, *, as_of: date | None = None) -> list[str]:
    failures = []
    text = answer.replace(',', '').replace('，', '，')
    for pattern in contract.get('required_patterns', []):
        if not re.search(pattern, text, re.S):
            failures.append('missing_fact:' + pattern)
    for pattern in contract.get('forbidden_patterns', []):
        if re.search(pattern, text, re.S):
            failures.append('forbidden_claim:' + pattern)
    # Check optional averages even when core totals are correct.
    for metric, expected in contract.get('weekly_averages', {}).items():
        unit = {'sets': '组', 'reps': '次', 'sessions': '次', 'volume_kg': 'kg'}[metric]
        segments = re.findall(r'(?:每周平均|周平均|周均)[^\n。]*', text)
        for segment in segments:
            values = re.findall(r'([\d.]+)\s*' + unit, segment)
            if metric == 'reps':
                # "周均2.25次、13.5组" reports sessions, not repetitions.
                # Accept explicit labels or a repetition count after sets;
                # absent/ambiguous metrics must not become fabricated errors.
                values = re.findall(r'([\d.]+)\s*次\s*(?:重复|动作)', segment)
                if not values:
                    values = re.findall(r'组[^\n。]*?([\d.]+)\s*次', segment)
            elif metric == 'sessions' and len(values) > 1:
                values = values[:1]
            if values and any(abs(float(v) - expected) > .05 for v in values):
                failures.append('incorrect_weekly_average:' + metric)
    if as_of:
        sunday = as_of + timedelta(days=6 - as_of.weekday())
        for y, m, d in re.findall(r'(?:本周日|周日)[^\d\n]{0,8}(?:(\d{4})[-年])?(\d{1,2})[-月](\d{1,2})', text):
            try:
                actual = date(int(y or as_of.year), int(m), int(d))
            except ValueError:
                actual = None
            if actual != sunday:
                failures.append('incorrect_current_sunday')
    return list(dict.fromkeys(failures))
