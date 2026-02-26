from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class ScadVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "scad_versions"

    chat_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
