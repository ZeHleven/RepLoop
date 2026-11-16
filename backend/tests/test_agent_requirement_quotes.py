"""Protect exact source attribution when parallel requirements share words."""
import pytest
from pydantic import ValidationError

from app.schemas.agent_task import TaskUpdate
from app.services.agent_task_state import advance_task_state


MESSAGE = '看看我近期训练和体重，安排今天的饭'


@pytest.mark.parametrize('quote', ['看看我近期体重', '近期训练和近期体重', '训练、体重'])
def test_reconstructed_or_discontinuous_quote_remains_rejected(quote):
    update = TaskUpdate(action='new', trigger=MESSAGE, requirements=[
        {'key': 'context_training', 'quote': '看看我近期训练'},
        {'key': 'context_weight', 'quote': quote},
    ])
    with pytest.raises(ValueError, match='task_requirement_not_current_user_quote'):
        advance_task_state(None, update, message=MESSAGE, run_id='synthetic-first', normalized_request=MESSAGE)


def test_empty_quote_rejected_before_task_state_update():
    with pytest.raises(ValidationError):
        TaskUpdate(action='new', trigger=MESSAGE, requirements=[{'key': 'context_weight', 'quote': ''}])


def test_shared_quote_keeps_independent_goals_and_allows_one_to_change():
    quote = '看看我近期训练和体重'
    update = TaskUpdate(action='new', trigger=MESSAGE, requirements=[
        {'key': 'context_training', 'quote': quote},
        {'key': 'context_weight', 'quote': quote},
        {'key': 'meal_plan_scope', 'quote': '安排今天的饭'},
    ])
    before = advance_task_state(None, update, message=MESSAGE, run_id='synthetic-first', normalized_request=MESSAGE)
    correction = '体重只参考本周，其他不变'
    patch = TaskUpdate(action='continue', task_id='synthetic-first', trigger=correction, requirements=[
        {'key': 'context_weight', 'quote': '体重只参考本周'},
    ])
    after = advance_task_state(before, patch, message=correction, run_id='synthetic-second', normalized_request='结合近期训练和本周体重安排今天的饭')
    requirements = {r.key: r for r in after.tasks[0].requirements}
    assert set(requirements) == {'context_training', 'context_weight', 'meal_plan_scope'}
    assert requirements['context_training'].quote == quote
    assert requirements['context_training'].source_run_id == 'synthetic-first'
    assert requirements['context_weight'].quote == '体重只参考本周'
    assert requirements['context_weight'].source_run_id == 'synthetic-second'
    assert requirements['meal_plan_scope'].quote == '安排今天的饭'
    assert before.tasks[0].requirements[1].quote == quote
