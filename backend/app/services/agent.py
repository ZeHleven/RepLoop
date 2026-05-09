import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.chat import ChatSession, ChatMessage
from app.models.profile import UserProfile
from app.services.knowledge import search_knowledge_base


SYSTEM_PROMPT = """你是一位专业的中文健身教练和营养顾问。根据用户的体能状况、目标和偏好，
提供个性化的训练建议和饮食方案。回答简洁专业，使用中文。"""


async def call_deepseek(messages: list[dict]) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={
                "model": settings.DEEPSEEK_CHAT_MODEL,
                "messages": messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _build_system_prompt(db: AsyncSession, user_id: str) -> str:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if not profile:
        return SYSTEM_PROMPT

    parts = [SYSTEM_PROMPT, "\n\n【用户档案】"]
    if profile.age:
        parts.append(f"年龄：{profile.age}岁")
    if profile.gender:
        parts.append(f"性别：{'男' if profile.gender == 'male' else '女'}")
    if profile.height_cm and profile.weight_kg:
        parts.append(f"身高体重：{profile.height_cm}cm / {profile.weight_kg}kg (BMI {profile.bmi}，{profile.bmi_category})")
    if profile.primary_goal:
        parts.append(f"主要目标：{profile.primary_goal}")
    if profile.experience_level:
        parts.append(f"训练经验：{profile.experience_level}")
    if profile.training_days_per_week:
        parts.append(f"每周训练天数：{profile.training_days_per_week}天")
    if profile.training_location:
        parts.append(f"训练地点：{profile.training_location}")
    if profile.diet_restriction:
        parts.append(f"饮食限制：{profile.diet_restriction}")
    return "\n".join(parts)


async def chat_with_agent(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> str:
    system_prompt = await _build_system_prompt(db, user_id)

    # RAG: 检索相关知识
    rag_chunks = await search_knowledge_base(db, query_text=user_message, limit=3)
    if rag_chunks:
        rag_text = "\n".join(f"- {c.content}" for c in rag_chunks)
        system_prompt += f"\n\n【相关知识】\n{rag_text}"

    # 加载历史消息（最近20条）
    history_rows = (await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(20)
    )).scalars().all()

    messages = [{"role": "system", "content": system_prompt}]
    for row in history_rows:
        messages.append({"role": row.role, "content": row.content})
    messages.append({"role": "user", "content": user_message})

    # 调用 DeepSeek
    reply = await call_deepseek(messages)

    # 保存消息
    db.add(ChatMessage(session_id=session_id, role="user", content=user_message))
    db.add(ChatMessage(session_id=session_id, role="assistant", content=reply))
    await db.commit()

    return reply
