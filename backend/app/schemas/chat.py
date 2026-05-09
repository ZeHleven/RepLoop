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
