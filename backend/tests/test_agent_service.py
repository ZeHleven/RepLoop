import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.services.agent import chat_with_agent
from app.config import settings
import bcrypt


def _make_user(user_id: str, email: str) -> User:
    pw = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    return User(id=user_id, email=email, password_hash=pw)


@pytest.mark.asyncio
async def test_chat_saves_user_and_assistant_messages(db_session):
    db_session.add(_make_user("agent-u1", "agent1@example.com"))
    await db_session.commit()

    session = ChatSession(user_id="agent-u1")
    db_session.add(session)
    await db_session.commit()

    mock_reply = "根据您的目标，建议每周训练3次。"
    with patch("app.services.agent.call_deepseek", new=AsyncMock(return_value=mock_reply)), \
         patch("app.services.agent.search_knowledge_base", new=AsyncMock(return_value=[])):
        reply = await chat_with_agent(
            db_session,
            user_id="agent-u1",
            session_id=session.id,
            user_message="我想减脂，该怎么训练？",
        )

    assert reply == mock_reply
    from sqlalchemy import select
    msgs = (await db_session.execute(
        select(ChatMessage).where(ChatMessage.session_id == session.id)
    )).scalars().all()
    roles = [m.role for m in msgs]
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_chat_includes_knowledge_in_context(db_session):
    db_session.add(_make_user("agent-u2", "agent2@example.com"))
    await db_session.commit()

    session = ChatSession(user_id="agent-u2")
    db_session.add(session)
    await db_session.commit()

    mock_chunks = [MagicMock(content="高蛋白饮食有助于增肌。", topic="nutrition")]
    with patch.object(settings, "RAG_ENABLED", True), \
         patch("app.services.agent.call_deepseek", new=AsyncMock(return_value="好的")) as mock_call, \
         patch("app.services.agent.search_knowledge_base", new=AsyncMock(return_value=mock_chunks)):
        await chat_with_agent(db_session, user_id="agent-u2", session_id=session.id, user_message="蛋白质重要吗？")

    call_args = mock_call.call_args
    messages = call_args.args[0]
    full_text = " ".join(m["content"] for m in messages)
    assert "高蛋白饮食有助于增肌" in full_text
