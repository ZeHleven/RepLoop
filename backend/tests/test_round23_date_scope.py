import pytest
from app.schemas.agent_task import TaskSnapshot,ConversationTask,TaskUpdate
from app.services.agent_intent import IntentResolution
from app.services.agent_task_state import is_calendar_query_revision

def context():
    state=TaskSnapshot(active_task_id='q',tasks=[ConversationTask(id='q',last_run_id='run1',request='查询2026年9月22日至25日进行中和放弃的训练',phase='responded')])
    return [{'role':'task_state','content':state.model_dump_json()},{'role':'assistant','content':'该范围没有记录','query_context':{'source_run_id':'run1','successful_query':True,'primary_intent':'workout_history_query','request_kind':'query','requested_effect':'read','risk_level':'low'}}]

@pytest.mark.parametrize('message,ok',[
 ('结束日期延长到9月26日，其他筛选条件不变。',True),
 ('开始日期提前到2026-09-21，其他条件保持。',True),
 ('截止日期改为2026年9月27日。',True),
 ('结束日期延长到9月26日，并删除记录。',False),
 ('结束训练日期延长到9月26日。',False),
 ('把这条训练记录的结束日期改为9月26日。',False),
 ('如果结束日期延长到9月26日会怎样？',False),
 ('不要把结束日期延长到9月26日。',False),
 ('结束日期延长到9月26日，其他筛选条件不变，保存。',False),
])
def test_date_operand_edit_requires_full_read_only_utterance(message,ok):
    r=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',confidence=.9,task_update=TaskUpdate(action='continue',task_id='q',trigger=message))
    assert is_calendar_query_revision(context(),r,message) is ok

@pytest.mark.parametrize('invalid',['no_context','no_success','stale_source','wrong_task','cancelled'])
def test_date_operand_exception_requires_verified_query(invalid):
    text='结束日期延长到9月26日，其他筛选条件不变。';ctx=context()
    r=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',confidence=.9,task_update=TaskUpdate(action='continue',task_id='q',trigger=text))
    if invalid=='no_context':ctx=[]
    elif invalid=='no_success':ctx[-1]['query_context']['successful_query']=False
    elif invalid=='stale_source':ctx[-1]['query_context']['source_run_id']='other'
    elif invalid=='wrong_task':r.task_update.task_id='other'
    else:
        state=TaskSnapshot.model_validate_json(ctx[0]['content']);state.tasks[0].phase='cancelled';ctx[0]['content']=state.model_dump_json()
    assert not is_calendar_query_revision(ctx,r,text)
