import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.chat import ChatSession, ChatMessage
from app.models.profile import UserProfile
from app.services.ai_client import AIServiceError, chat_completion
from app.services.knowledge import search_knowledge_base

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是一位专业的中文健身教练和营养顾问。根据用户的体能状况、目标和偏好，
提供个性化的训练建议和饮食方案。回答简洁专业，使用中文。"""


async def call_deepseek(messages: list[dict]) -> str:
    return await chat_completion(
        messages,
        model=settings.DEEPSEEK_CHAT_MODEL,
        max_tokens=1024,
        temperature=0.7,
        thinking=False,
    )


async def _build_system_prompt(db: AsyncSession, user_id: str) -> str:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if not profile:
        return SYSTEM_PROMPT

    parts = [SYSTEM_PROMPT, "\n\n【用户档案】"]
    if profile.age:
        parts.append(f"年龄：{profile.age}岁")
    if profile.gender:
        gender_labels = {
            "male": "男",
            "female": "女",
            "prefer_not_to_say": "未透露",
        }
        parts.append(f"性别：{gender_labels.get(profile.gender, '未透露')}")
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
    if profile.injuries:
        parts.append(f"伤病情况：{json.dumps(profile.injuries, ensure_ascii=False)}")
    if profile.chronic_conditions:
        parts.append(
            f"慢性疾病：{json.dumps(profile.chronic_conditions, ensure_ascii=False)}"
        )
    if profile.injuries or profile.chronic_conditions:
        parts.append(
            "安全要求：避免诊断或替代医疗建议；涉及伤病、胸痛、眩晕、呼吸困难或慢性病风险时，"
            "应建议用户先咨询医生或合格专业人士。"
        )
    return "\n".join(parts)


async def chat_with_agent(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> str:
    system_prompt = await _build_system_prompt(db, user_id)

    # RAG is optional because DeepSeek currently exposes chat models only.
    rag_chunks = []
    if settings.RAG_ENABLED:
        try:
            rag_chunks = await search_knowledge_base(
                db, query_text=user_message, limit=3
            )
        except AIServiceError as exc:
            logger.warning("RAG lookup skipped: %s", exc)
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
