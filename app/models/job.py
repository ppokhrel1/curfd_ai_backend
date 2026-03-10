from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class Job(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "jobs"

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    status: Mapped[str] = mapped_column(String(50), default="queued")
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_image_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_format: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    session = relationship("Session", back_populates="jobs")
    assets = relationship("Asset", back_populates="job", cascade="all, delete-orphan")
