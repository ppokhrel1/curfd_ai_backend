from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class Chat(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "chats"
    __table_args__ = (
        Index("ix_chats_session_updated", "session_id", "updated_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    session = relationship("Session", back_populates="chats")
    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")
