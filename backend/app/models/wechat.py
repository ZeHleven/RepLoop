import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WeChatIdentity(Base):
    __tablename__ = "wechat_identities"
    __table_args__ = (
        UniqueConstraint("app_id", "open_id", name="uq_wechat_identity_app_openid"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    app_id: Mapped[str] = mapped_column(String(32))
    open_id: Mapped[str] = mapped_column(String(64))
    union_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
