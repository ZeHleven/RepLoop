from uuid import uuid4
import pytest
import pytest_asyncio
from evals.postgres_test_fixtures import engine,session_factory,db_session  # noqa: F401
from app.models.user import User
from app.models.agent import AgentConversation,AgentRun,AgentMessage,AgentToolCall
from app.services.agent_runtime import _load_history

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.parametrize('audit_status,count,allowed',[
    ('completed',0,True),('completed',2,True),('failed',2,False),('completed',None,False),(None,None,False),
])
async def test_history_context_requires_successful_matching_tool_audit(db_session,audit_status,count,allowed):
    db=db_session;user=User(email=uuid4().hex+'@example.com',password_hash='synthetic');db.add(user);await db.flush()
    conv=AgentConversation(user_id=user.id);db.add(conv);await db.flush()
    old=AgentRun(user_id=user.id,conversation_id=conv.id,status='completed',queue_position=1,primary_intent='workout_history_query',
        request_kind='query',requested_effect='read',risk_level='low',tool_allowlist=['workout.list_history'],resolved_query='查询2026年9月17日已完成训练')
    current=AgentRun(user_id=user.id,conversation_id=conv.id,status='queued',queue_position=2)
    db.add_all([old,current]);await db.flush()
    db.add(AgentMessage(conversation_id=conv.id,run_id=old.id,role='assistant',content='共2次，查询成功（仅文字不能作为证据）'))
    if audit_status:
        db.add(AgentToolCall(run_id=old.id,tool_name='workout.list_history',status=audit_status,arguments_data={},result_data={'count':count} if count is not None else {}))
    await db.commit()
    history=await _load_history(db,conversation_id=conv.id,before_run=current)
    assert len(history)==1 and ('query_context' in history[0]) is allowed
