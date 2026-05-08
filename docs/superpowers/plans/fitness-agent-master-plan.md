# Fitness Agent — 完整项目规划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个基于 AI 的中文健身助手应用，后端为 FastAPI + PostgreSQL + pgvector + Redis，AI 推理使用 DeepSeek，前端为 Flutter。

**Architecture:** 分层架构——数据层（PostgreSQL + pgvector + Redis）→ 服务层（FastAPI）→ AI 层（DeepSeek Agent + RAG）→ 前端（Flutter）。

**Tech Stack:** Python 3.13 · FastAPI · SQLAlchemy 2.0 async · asyncpg · pgvector · Redis · DeepSeek API · Flutter 3.x · Docker Compose · Caddy

---

## 项目整体进度

| Plan | 内容 | 状态 |
|------|------|------|
| Plan 1 | 核心基础设施（认证、Profile、BMI、JWT） | ✅ 完成 |
| Plan 2 | 核心数据资产（Exercise、Food、Knowledge、Embedding、Seed） | ✅ 完成 |
| Plan 3 | AI 对话层（DeepSeek Agent、RAG、训练计划生成） | 🔲 待开始 |
| Plan 4 | 训练计划管理（WorkoutPlan、WorkoutSession） | 🔲 待开始 |
| Plan 5 | 饮食记录（MealLog、每日营养汇总） | 🔲 待开始 |
| Plan 6 | Flutter 前端 | 🔲 待开始 |
| Plan 7 | 生产部署（CI/CD、监控、HTTPS） | 🔲 待开始 |

---

## 当前已实现内容（Plan 1 + Plan 2）

### 已有 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/auth/register | 注册（email 或 phone） |
| POST | /api/v1/auth/login | 登录 |
| POST | /api/v1/auth/refresh | 刷新 access token |
| GET | /api/v1/profile | 获取用户档案 |
| PUT | /api/v1/profile | 更新档案（自动计算 BMI） |
| POST | /api/v1/profile/weight | 记录体重 |
| GET | /api/v1/me | 当前用户信息 |
| GET | /api/v1/exercises | 动作库查询（过滤：肌群/器械/难度/动作模式/分类） |
| GET | /api/v1/foods | 食物库查询（过滤：分类/饮食标签/最低蛋白质） |

### 已有数据模型

- `User` — 邮箱/手机注册，bcrypt 密码
- `UserProfile` — 身高体重年龄性别目标训练偏好，BMI 自动计算（亚洲标准）
- `WeightLog` — 体重记录历史
- `Exercise` — 动作库（20条种子数据），JSONB 肌群/器械
- `Food` — 食物营养库（20条种子数据），JSONB 饮食标签
- `KnowledgeChunk` — 知识库片段，Vector(2048) embedding

### 已有服务

- `auth.py` — JWT 令牌（access 30min + refresh 30day）
- `profile.py` — BMI 计算（亚洲阈值：正常<24，超重24-28，肥胖≥28）
- `exercise.py` — `query_exercise_library()` 多条件过滤
- `food.py` — `query_nutrition_database()` 多条件过滤
- `embedding.py` — `get_embedding()` 调用 DeepSeek embedding API
- `knowledge.py` — `add_knowledge_chunk()` + `search_knowledge_base()` cosine 检索

### 已有基础设施

- Docker Compose: postgres（pgvector）+ redis + fastapi + caddy
- Alembic 迁移: 0001（users/profiles/weight_logs）+ 0002（exercises/foods/knowledge_chunks）
- 54 个测试全部通过
- `scripts/seed_exercises.py` — 20个动作（幂等）
- `scripts/seed_foods.py` — 20个食物（幂等）
- `scripts/import_knowledge.py` — 知识库导入 CLI

---

## Plan 3：AI 对话层

**Goal:** 接入 DeepSeek Chat API，实现多轮对话、RAG 知识检索、训练计划生成、饮食建议。

**File Structure:**
```
backend/
  app/
    models/
      chat.py          # ChatSession, ChatMessage
    services/
      agent.py         # DeepSeek 对话 + tool calling
      planner.py       # 训练计划生成逻辑
      nutritionist.py  # 饮食建议逻辑
    routers/
      chat.py          # POST /api/v1/chat, GET /api/v1/chat/history
    schemas/
      chat.py          # ChatRequest, ChatResponse, MessageResponse
  alembic/versions/
    0003_chat.py       # chat_sessions + chat_messages 表
  tests/
    test_chat_service.py
    test_chat_router.py
```

---

### Task 1：ChatSession + ChatMessage 数据模型 + 迁移 0003

**Files:**
- Create: `backend/app/models/chat.py`
- Create: `backend/alembic/versions/0003_chat.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: 创建模型**

```python
# backend/app/models/chat.py
import uuid
from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))      # "user" | "assistant" | "system"
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: 更新 models/__init__.py**

```python
from app.models.user import User
from app.models.profile import UserProfile, WeightLog
from app.models.exercise import Exercise
from app.models.food import Food
from app.models.knowledge import KnowledgeChunk
from app.models.chat import ChatSession, ChatMessage
```

- [ ] **Step 3: 创建 Alembic 迁移 0003**

```python
# backend/alembic/versions/0003_chat.py
"""chat sessions and messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
```

- [ ] **Step 4: 写失败测试**

```python
# backend/tests/test_chat_models.py
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
```

- [ ] **Step 5: 运行测试确认通过**

```powershell
$env:PYTHONPATH="."; $env:SECRET_KEY="testsecret"; $env:DATABASE_URL="postgresql+asyncpg://fitness:fitness_pass@localhost:5432/fitness_test"; python -m pytest tests/test_chat_models.py -v
```

- [ ] **Step 6: 提交**

```bash
git add app/models/chat.py app/models/__init__.py alembic/versions/0003_chat.py tests/test_chat_models.py
git commit -m "feat: ChatSession + ChatMessage models + migration 0003"
```

---

### Task 2：Agent service（DeepSeek 多轮对话 + RAG）

**Files:**
- Create: `backend/app/services/agent.py`
- Create: `backend/tests/test_agent_service.py`

Agent 设计：
- 接收 user_id + session_id + user_message
- 从数据库加载历史消息（最近 20 条）
- 构建 system prompt（含用户档案信息）
- 调用 RAG：`search_knowledge_base()` 检索相关知识（top-3）
- 调用 DeepSeek Chat API（httpx）
- 保存 user + assistant 消息到数据库
- 返回 assistant 回复

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_service.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.models.chat import ChatSession, ChatMessage
from app.services.agent import chat_with_agent


@pytest.mark.asyncio
async def test_chat_saves_user_and_assistant_messages(db_session):
    session = ChatSession(user_id="u1")
    db_session.add(session)
    await db_session.commit()

    mock_reply = "根据您的目标，建议每周训练3次。"
    with patch("app.services.agent.call_deepseek", new=AsyncMock(return_value=mock_reply)), \
         patch("app.services.agent.search_knowledge_base", new=AsyncMock(return_value=[])):
        reply = await chat_with_agent(
            db_session,
            user_id="u1",
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
    session = ChatSession(user_id="u2")
    db_session.add(session)
    await db_session.commit()

    mock_chunks = [MagicMock(content="高蛋白饮食有助于增肌。", topic="nutrition")]
    with patch("app.services.agent.call_deepseek", new=AsyncMock(return_value="好的")) as mock_call, \
         patch("app.services.agent.search_knowledge_base", new=AsyncMock(return_value=mock_chunks)):
        await chat_with_agent(db_session, user_id="u2", session_id=session.id, user_message="蛋白质重要吗？")

    call_args = mock_call.call_args
    messages = call_args.args[0]
    full_text = " ".join(m["content"] for m in messages)
    assert "高蛋白饮食有助于增肌" in full_text
```

- [ ] **Step 2: 实现 agent.py**

```python
# backend/app/services/agent.py
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
```

- [ ] **Step 3: 在 config.py 添加 DeepSeek 配置**

```python
# 在 Settings 类中添加：
DEEPSEEK_API_KEY: str = ""
DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
DEEPSEEK_CHAT_MODEL: str = "deepseek-chat"
```

- [ ] **Step 4: 运行测试**

```powershell
$env:PYTHONPATH="."; $env:SECRET_KEY="testsecret"; $env:DATABASE_URL="postgresql+asyncpg://fitness:fitness_pass@localhost:5432/fitness_test"; python -m pytest tests/test_agent_service.py -v
```

- [ ] **Step 5: 提交**

```bash
git add app/services/agent.py app/config.py tests/test_agent_service.py
git commit -m "feat: agent service with DeepSeek chat + RAG knowledge retrieval"
```

---

### Task 3：Chat schemas + Chat router

**Files:**
- Create: `backend/app/schemas/chat.py`
- Create: `backend/app/routers/chat.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_chat_router.py`

API 设计：
- `POST /api/v1/chat` — 发送消息（自动创建 session 或使用现有 session）
- `GET /api/v1/chat/sessions` — 获取用户所有会话列表
- `GET /api/v1/chat/sessions/{session_id}/messages` — 获取某会话消息历史

- [ ] **Step 1: 创建 schemas**

```python
# backend/app/schemas/chat.py
from pydantic import BaseModel
from datetime import datetime


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # None 则自动创建新会话


class ChatResponse(BaseModel):
    reply: str
    session_id: str


class SessionResponse(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    model_config = {"from_attributes": True}
```

- [ ] **Step 2: 创建 router**

```python
# backend/app/routers/chat.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage
from app.schemas.chat import ChatRequest, ChatResponse, SessionResponse, MessageResponse
from app.services.agent import chat_with_agent

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.session_id:
        session = await db.scalar(
            select(ChatSession).where(
                ChatSession.id == body.session_id,
                ChatSession.user_id == current_user.id,
            )
        )
        if not session:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="会话不存在")
    else:
        session = ChatSession(
            user_id=current_user.id,
            title=body.message[:50],
        )
        db.add(session)
        await db.flush()

    reply = await chat_with_agent(
        db,
        user_id=current_user.id,
        session_id=session.id,
        user_message=body.message,
    )
    return ChatResponse(reply=reply, session_id=session.id)


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
    )).scalars().all()
    return rows


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
    )
    if not session:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = (await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )).scalars().all()
    return rows
```

- [ ] **Step 3: 注册路由**

```python
# backend/app/main.py 新增：
from app.routers import auth, profile, exercises, foods, chat
app.include_router(chat.router, prefix="/api/v1")
```

- [ ] **Step 4: 写测试**

```python
# backend/tests/test_chat_router.py
import pytest
from unittest.mock import patch, AsyncMock


async def get_token(client, email):
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": "pass1234"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_chat_creates_new_session(client):
    token = await get_token(client, "chat1@example.com")
    with patch("app.routers.chat.chat_with_agent", new=AsyncMock(return_value="你好！")):
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "你好"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "你好！"
    assert data["session_id"] is not None


@pytest.mark.asyncio
async def test_chat_with_existing_session(client):
    token = await get_token(client, "chat2@example.com")
    with patch("app.routers.chat.chat_with_agent", new=AsyncMock(return_value="继续")):
        r1 = await client.post("/api/v1/chat", json={"message": "第一条"}, headers={"Authorization": f"Bearer {token}"})
        session_id = r1.json()["session_id"]
        r2 = await client.post(
            "/api/v1/chat",
            json={"message": "第二条", "session_id": session_id},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_list_sessions(client):
    token = await get_token(client, "chat3@example.com")
    with patch("app.routers.chat.chat_with_agent", new=AsyncMock(return_value="ok")):
        await client.post("/api/v1/chat", json={"message": "msg1"}, headers={"Authorization": f"Bearer {token}"})
        await client.post("/api/v1/chat", json={"message": "msg2"}, headers={"Authorization": f"Bearer {token}"})
    resp = await client.get("/api/v1/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_get_messages_wrong_session_returns_404(client):
    token = await get_token(client, "chat4@example.com")
    resp = await client.get(
        "/api/v1/chat/sessions/nonexistent-id/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 5: 运行测试**

```powershell
$env:PYTHONPATH="."; $env:SECRET_KEY="testsecret"; $env:DATABASE_URL="postgresql+asyncpg://fitness:fitness_pass@localhost:5432/fitness_test"; python -m pytest tests/test_chat_router.py tests/test_agent_service.py tests/test_chat_models.py -v
```

- [ ] **Step 6: 提交**

```bash
git add app/schemas/chat.py app/routers/chat.py app/main.py tests/test_chat_router.py
git commit -m "feat: chat router with session management (POST /chat, GET /chat/sessions)"
```

---

## Plan 4：训练计划管理

**Goal:** 用户可以拥有 AI 生成或手动创建的训练计划，记录每次训练完成情况。

**File Structure:**
```
backend/
  app/
    models/workout.py        # WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
    services/planner.py      # generate_workout_plan() AI 生成
    routers/workouts.py      # CRUD + log endpoints
    schemas/workout.py
  alembic/versions/0004_workouts.py
  tests/
    test_workout_service.py
    test_workout_router.py
```

---

### Task 4：WorkoutPlan 数据模型 + 迁移 0004

**数据模型设计：**

```python
# backend/app/models/workout.py
import uuid
from datetime import datetime, date
from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    goal: Mapped[str | None] = mapped_column(String(50), nullable=True)        # "减脂" | "增肌" | "塑形"
    duration_weeks: Mapped[int] = mapped_column(Integer, default=4)
    days_per_week: Mapped[int] = mapped_column(Integer, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlannedExercise(Base):
    """单个训练计划中某天的某个动作安排"""
    __tablename__ = "planned_exercises"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("workout_plans.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)     # 1=周一 … 7=周日
    exercise_id: Mapped[str] = mapped_column(String, ForeignKey("exercises.id"))
    sets: Mapped[int] = mapped_column(Integer, default=3)
    reps: Mapped[str] = mapped_column(String(20), default="10")   # "8-12" 或 "60s"
    rest_seconds: Mapped[int] = mapped_column(Integer, default=90)
    order_index: Mapped[int] = mapped_column(Integer, default=0)


class WorkoutSession(Base):
    """用户实际完成的一次训练记录"""
    __tablename__ = "workout_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(String, ForeignKey("workout_plans.id"), nullable=True)
    trained_at: Mapped[date] = mapped_column(Date, index=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionExercise(Base):
    """训练记录中某个动作的实际完成数据"""
    __tablename__ = "session_exercises"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("workout_sessions.id"), index=True)
    exercise_id: Mapped[str] = mapped_column(String, ForeignKey("exercises.id"))
    sets_data: Mapped[list] = mapped_column(JSONB)   # [{"reps": 10, "weight_kg": 60}, ...]
```

**API 端点：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/workouts/plans | 创建训练计划 |
| GET | /api/v1/workouts/plans | 获取用户训练计划列表 |
| GET | /api/v1/workouts/plans/{id} | 获取计划详情（含动作安排） |
| DELETE | /api/v1/workouts/plans/{id} | 删除计划 |
| POST | /api/v1/workouts/plans/generate | AI 生成训练计划 |
| POST | /api/v1/workouts/sessions | 记录训练 |
| GET | /api/v1/workouts/sessions | 获取训练历史 |

**实现步骤：** 同 Plan 3 结构，创建模型→迁移→service→router→测试→提交。

---

### Task 5：planner service（AI 生成训练计划）

`generate_workout_plan()` 接收用户档案，调用 DeepSeek 生成结构化训练计划（JSON），解析后存储为 `WorkoutPlan` + `PlannedExercise` 记录。

Prompt 设计要点：
- 要求 AI 返回严格的 JSON 格式
- 包含用户目标、经验水平、每周训练天数、训练地点
- 从动作库随机选取匹配的动作（先查 DB）

---

## Plan 5：饮食记录

**Goal:** 用户可以记录每日饮食，系统自动汇总热量和宏量营养素。

**File Structure:**
```
backend/
  app/
    models/meal.py           # MealLog, MealItem
    services/nutritionist.py # 饮食分析 + AI 建议
    routers/meals.py         # CRUD
    schemas/meal.py
  alembic/versions/0005_meals.py
  tests/
    test_meal_service.py
    test_meal_router.py
```

**数据模型：**

```python
class MealLog(Base):
    __tablename__ = "meal_logs"
    id: Mapped[str] = ...
    user_id: Mapped[str] = ...
    logged_at: Mapped[date] = ...          # 饮食日期
    meal_type: Mapped[str] = ...           # "早餐" | "午餐" | "晚餐" | "加餐"


class MealItem(Base):
    __tablename__ = "meal_items"
    id: Mapped[str] = ...
    meal_id: Mapped[str] = ...
    food_id: Mapped[str | None] = ...      # 关联食物库
    food_name: Mapped[str] = ...           # 自由文本（food_id 为空时使用）
    amount_g: Mapped[float] = ...          # 克数
    calories: Mapped[float] = ...          # 计算值
    protein_g: Mapped[float] = ...
    carbs_g: Mapped[float] = ...
    fat_g: Mapped[float] = ...
```

**API 端点：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/meals | 记录一餐（含食物列表） |
| GET | /api/v1/meals/today | 今日饮食汇总 |
| GET | /api/v1/meals/history | 历史记录（按日期） |
| DELETE | /api/v1/meals/{id} | 删除记录 |

---

## Plan 6：Flutter 前端

**Tech Stack:** Flutter 3.x · Riverpod（状态管理）· go_router（路由）· Dio（HTTP）

**File Structure:**
```
frontend/
  lib/
    main.dart
    core/
      api/         api_client.dart, auth_interceptor.dart
      router/      router.dart
      theme/        theme.dart
    features/
      auth/         login_page.dart, register_page.dart
      onboarding/   onboarding_flow.dart (5步问卷)
      chat/         chat_page.dart, chat_bubble.dart
      profile/      profile_page.dart, bmi_card.dart
      exercises/    exercise_list_page.dart, exercise_card.dart
      workouts/     workout_plan_page.dart, log_workout_page.dart
      meals/        meal_log_page.dart, food_search.dart
```

**屏幕列表：**

1. **登录 / 注册** — 邮箱+密码，JWT 存 secure storage
2. **Onboarding（5步）** — 性别→年龄身高体重→目标→经验水平→训练偏好
3. **首页（Tab）** — 聊天 · 训练 · 饮食 · 我的
4. **AI 聊天界面** — 气泡样式，支持多会话
5. **训练计划** — 查看/生成计划，每日动作列表
6. **训练记录** — 打卡，输入重量/次数
7. **饮食记录** — 搜索食物，按餐添加，日汇总
8. **个人档案** — BMI 卡片，体重趋势图，设置

---

## Plan 7：生产部署

**目标：** 可在 VPS（4GB RAM）上稳定运行，HTTPS，自动备份，健康监控。

### Task：完善 Docker Compose 生产配置

当前 `docker-compose.yml` 已有基础结构，需要补充：

- [ ] `backend/Dockerfile` — 多阶段构建（builder + runtime），确认 `alembic upgrade head` 作为启动前命令
- [ ] 数据库定期备份脚本 `scripts/backup_db.sh`
- [ ] `Caddyfile` — 配置域名 + 自动 HTTPS
- [ ] `.env.production` 模板（SECRET_KEY、DEEPSEEK_API_KEY 等）
- [ ] `docker-compose.prod.yml` — 覆盖开发配置（去掉端口暴露、加 restart 策略）
- [ ] GitHub Actions `.github/workflows/test.yml` — push 触发运行 pytest

### Task：知识库初始化

- [ ] 编写 `knowledge/nutrition_basics.txt` — 营养学基础知识（30+ 段落）
- [ ] 编写 `knowledge/training_principles.txt` — 训练原理（30+ 段落）
- [ ] 编写 `scripts/init_knowledge.sh` — 一键导入两个文件
- [ ] 更新 `README.md` 部署步骤

---

## 关键依赖关系

```
Plan 1 (认证+Profile) ──→ Plan 3 (AI对话，需要 user_id + profile)
                      ──→ Plan 4 (训练计划，需要 user_id + profile)
                      ──→ Plan 5 (饮食记录，需要 user_id)

Plan 2 (数据资产)      ──→ Plan 3 (RAG 需要 knowledge_chunks)
                      ──→ Plan 4 (训练计划需要 exercises 库)
                      ──→ Plan 5 (饮食记录需要 foods 库)

Plan 3+4+5 (后端完整)  ──→ Plan 6 (Flutter 前端消费所有 API)

Plan 1-6 (全部完成)    ──→ Plan 7 (部署上线)
```

---

## 配置项汇总

`.env` 中需要配置的所有环境变量：

```env
# 数据库
DATABASE_URL=postgresql+asyncpg://fitness:fitness_pass@postgres:5432/fitness_db
REDIS_URL=redis://redis:6379

# 认证
SECRET_KEY=<64字节随机字符串>
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=30
ALGORITHM=HS256

# DeepSeek AI
DEEPSEEK_API_KEY=<your-key>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_CHAT_MODEL=deepseek-chat

# Embedding（DeepSeek）
EMBEDDING_API_KEY=<your-key>
EMBEDDING_BASE_URL=https://api.deepseek.com
EMBEDDING_MODEL=deepseek-embedding
EMBEDDING_DIM=2048

# Docker（供 docker-compose 使用）
POSTGRES_USER=fitness
POSTGRES_PASSWORD=fitness_pass
POSTGRES_DB=fitness_db
```

---

## 下一步：执行 Plan 3

从 **Task 1（ChatSession + ChatMessage 模型 + 迁移 0003）** 开始。

运行方式：
```
/subagent-driven-development
```
或直接告知「开始 Plan 3」。
