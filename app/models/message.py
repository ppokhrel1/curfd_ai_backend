from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class Message(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "messages"

    chat_id: Mapped[str] = mapped_column(String(36), ForeignKey("chats.id"), index=True)
    role: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    chat = relationship("Chat", back_populates="messages")
