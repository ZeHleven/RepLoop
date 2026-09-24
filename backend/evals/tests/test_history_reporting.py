from copy import deepcopy
import json
from unittest.mock import patch
import pytest
from pathlib import Path
from app.services.workout_history_reporting import history_limit, render_history
from evals.judge_eval import snapshot_result, score_judgment, Judgment
from evals.judge_eval import EvalCase, fixture_tools
from app.services.agent_intent import IntentResolution
from app.services.agent_runtime import invoke_langchain_agent

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = json.loads((ROOT / 'artifacts/agent-evals/task-benchmark-20260922/source-snapshot.json').read_text(encoding='utf-8'))


def test_history_limits_are_bounded_and_open_questions_defer():
    assert history_limit('看最近三次训练，比较深蹲') == 3
    assert history_limit('列出最近21次训练') is None
    assert history_limit('最近3次训练，安排下周计划') is None


def test_action_report_uses_actual_sets_not_target_weight():
    data = snapshot_result(SNAPSHOT, 'workout.list_history', {'limit': 3})
    for row in data['sessions']:
        for ex in row['exercises']:
            ex['target_weight_kg'] = 999
    answer = render_history(data, '比较最近3次训练的深蹲')
    assert '45kg×10次×3组' in answer and '50kg×10次×3组' in answer
    assert '1,350kg' in answer and '1,500kg' in answer and '变化5kg' in answer
    assert '999' not in answer and '计划' not in answer


def test_recent_summary_keeps_descending_dates_and_whole_session_volume():
    data = snapshot_result(SNAPSHOT, 'workout.list_history', {'limit': 3})
    answer = render_history(data, '列出最近3次训练，每次日期组数容量')
    assert answer.index('2026-09-21') < answer.index('2026-09-18') < answer.index('2026-09-14')
    assert '2,250kg' in answer and '2,100kg' in answer


def test_unknown_action_does_not_get_other_exercises_answer():
    data = snapshot_result(SNAPSHOT, 'workout.list_history', {'limit': 3})
    assert render_history(data, '比较最近3次卧推') is None


def test_no_records_and_mixed_sets_are_not_fabricated():
    assert '无法比较' in render_history({'sessions': []}, '比较深蹲')
    data = snapshot_result(SNAPSHOT, 'workout.list_history', {'limit': 1})
    sets = data['sessions'][0]['exercises'][0]['sets_data']
    sets[0]['weight_kg'] = 40
    answer = render_history(data, '比较深蹲')
    assert '40kg×10次×1组' in answer and '50kg×10次×2组' in answer


def test_a_high_total_cannot_hide_a_grounding_failure():
    payload = {'dimensions': {key: {'score': 2, 'reason': 'test', 'evidence_refs': ['answer']}
                            for key in ['grounding', 'completion', 'context', 'clarity']},
               'hard_failures': [], 'summary': 'test', 'needs_human_review': False}
    payload['dimensions']['grounding']['score'] = 1
    score = score_judgment(Judgment.model_validate(payload), [], 80)
    assert score['score'] == 80 and not score['passed']


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [['workout.list_history'], ['workout.get_progress', 'workout.list_history']])
async def test_history_report_does_not_depend_on_model_routing_style(allowed):
    case = EvalCase(id='history-routing', category='regression', message='看最近3次训练里的深蹲，比较重量和容量',
                    expectations=['actual-set comparison'], business_snapshot=SNAPSHOT)
    trace = []
    with patch('app.services.agent_runtime.build_read_tools', side_effect=lambda *a, **kw: fixture_tools(case, kw['allowlist'], trace)), patch(
        'app.services.agent_runtime._build_model', side_effect=AssertionError('Unexpected free-form model call')):
        result = await invoke_langchain_agent(None, user_id='synthetic', history=[], user_message=case.message,
                                             tool_allowlist=allowed, resolution=IntentResolution(
            primary_intent='workout_history_query', intent_domain='workout_history',
            evidence_requirements=['workout_history'], resolved_query=case.message, confidence=.95))
    assert result['response_mode'] == 'verified_history_report'
    assert [(row['tool'], row['arguments']) for row in trace] == [('workout.list_history', {'limit': 3})]
