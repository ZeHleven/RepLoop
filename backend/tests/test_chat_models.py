from app.models.chat import ChatSession, ChatMessage


def test_chat_session_instantiation():
    s = ChatSession(user_id="u1", title="My Chat")
    assert s.user_id == "u1"
    assert s.id is not None


def test_chat_message_instantiation():
    m = ChatMessage(session_id="s1", role="user", content="你好")
    assert m.role == "user"
    assert m.content == "你好"
    assert m.id is not None
