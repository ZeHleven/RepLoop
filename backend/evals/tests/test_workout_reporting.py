import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.services.workout_reporting import REPORT_DATE, enrich_progress, progress_request, render_progress
from app.services.agent_intent import IntentResolution, route_tools, normalize_resolution
from app.services.agent_intent_model import resolve_intent_with_fallback
from evals.judge_eval import EvalCase, fixture_tools, snapshot_result, score_judgment, Judgment
from evals.answer_checks import check_answer
from app.services.agent_runtime import invoke_langchain_agent, _extract_agent_output

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = json.loads((ROOT / 'artifacts/agent-evals/task-benchmark-20260922/source-snapshot.json').read_text(encoding='utf-8'))
TODAY = date(2026, 9, 22)


@pytest.mark.parametrize('query,weeks,start,end', [
    ('查询上周', 2, '2026-09-14', '2026-09-20'),
    ('查询上上周', 3, '2026-09-07', '2026-09-13'),
    ('最近四周训练量', 4, '2026-08-31', '2026-09-27'),
    ('本周与上周比较', 2, '2026-09-14', '2026-09-27'),
    ('今天是2026年9月22日。比较9月7日和9月14日开始的两个完整周', 3, '2026-09-07', '2026-09-20'),
])
def test_scope_and_minimum_fetch(query, weeks, start, end):
    plan = progress_request(query, TODAY)
    assert (plan.weeks, str(plan.start), str(plan.end)) == (weeks, start, end)


def test_year_boundary_and_unsupported_ranges():
    assert str(progress_request('上周', date(2026, 1, 1)).start) == '2025-12-22'
    assert progress_request('最近53周', TODAY) is None
    assert progress_request('比较9月31日与10月7日', TODAY) is None


def test_server_arithmetic_and_calendar():
    data = snapshot_result(SNAPSHOT, 'workout.get_progress', {'weeks': 4})
    assert data['averages_per_calendar_week'] == {'sessions': 2.25, 'sets': 13.5, 'reps': 135, 'volume_kg': 4425}
    assert [row['is_complete'] for row in data['weekly']] == [True, True, True, False]
    text = render_progress(data, progress_request('最近4周', TODAY), '最近4周')
    assert '13.5组' in text and '135次重复' in text and '2026-09-27' in text
    assert '连续两周下滑' not in text


def test_comparisons_only_between_complete_weeks_and_zero_base():
    data = snapshot_result(SNAPSHOT, 'workout.get_progress', {'weeks': 3})
    query = '比较9月7日和9月14日开始的两个完整周'
    answer = render_progress(data, progress_request(query, TODAY), query)
    assert '下降33.3%' in answer and '下降28.2%' in answer
    data['weekly'][0]['sessions'] = 0
    answer = render_progress(data, progress_request(query, TODAY), query)
    assert '基期为0，不计算百分比' in answer
    partial = render_progress(data, progress_request('本周比上周退步了吗', TODAY), '本周比上周退步了吗')
    assert '完整周比较' not in partial and '不能用部分周' in partial


@pytest.mark.parametrize('weeks', [1, 2, 4, 7, 13, 52])
def test_fixture_accepts_all_legal_progress_ranges(weeks):
    data = snapshot_result(SNAPSHOT, 'workout.get_progress', {'weeks': weeks})
    assert len(data['weekly']) == weeks
    assert data['total_sets'] == sum(row['sets'] for row in data['weekly'])


@pytest.mark.parametrize('limit', [1, 3, 5, 9, 10, 20])
def test_fixture_history_uses_same_snapshot(limit):
    data = snapshot_result(SNAPSHOT, 'workout.list_history', {'limit': limit})
    assert data['count'] == min(limit, 9)
    assert data['sessions'][0]['trained_at'] == '2026-09-21'


def test_gates_catch_original_false_averages_and_sunday_without_forcing_wording():
    contract = {'weekly_averages': {'sets': 13.5, 'reps': 135}}
    assert check_answer('每周平均约9组、90次。', contract)
    assert not check_answer('每周平均13.5组、135次。', contract)
    assert check_answer('本周日（09-28）结束再看', {}, as_of=TODAY) == ['incorrect_current_sunday']
    assert not check_answer('本周日（09-27）结束再看', {}, as_of=TODAY)


def test_weekly_session_average_is_not_misread_as_repetitions():
    contract = {'weekly_averages': {'sets':13.5, 'reps':135}}
    assert not check_answer('周均 2.25 次、13.5 组', contract)
    assert not check_answer('周平均 2.25次训练、13.5组、135次重复', contract)
    assert 'incorrect_weekly_average:reps' in check_answer('周平均 13.5组、90次重复', contract)






def test_no_data_gate_accepts_equivalent_phrases():
    contract = {'required_patterns': [r'(无法|不能|没有可用于).*(判断|进步)']}
    for phrase in ['没有可用于判断进步或退步的数据', '无法判断是否进步', '不能判断进步']:
        assert not check_answer(phrase, contract)
    assert check_answer('你肯定进步了', contract)




@pytest.mark.asyncio
async def test_verified_report_makes_one_actual_tool_call_and_auditable_messages():
    case = EvalCase(id='report', category='unit', message='查询上周训练次数', expectations=['test'],
                    business_snapshot=SNAPSHOT)
    trace = []
    token = REPORT_DATE.set(TODAY)
    try:
        with patch('app.services.agent_runtime.build_read_tools', side_effect=lambda *a, **kw: fixture_tools(case, kw['allowlist'], trace)), patch(
            'app.services.agent_runtime._build_model') as model:
            result = await invoke_langchain_agent(None, user_id='synthetic', history=[],
                user_message=case.message, tool_allowlist=['workout.get_progress'],
                resolution=IntentResolution(primary_intent='workout_progress_query', intent_domain='workout_progress',
                    evidence_requirements=['workout_progress'], resolved_query=case.message, confidence=.95))
        assert [call['arguments'] for call in trace] == [{'weeks': 2}]
        answer, cards = _extract_agent_output(result)
        assert '2次训练、12组' in answer and '4,200kg' in answer
        assert cards[0]['type'] == 'workout.get_progress'
        model.assert_not_called()
    finally:
        REPORT_DATE.reset(token)
