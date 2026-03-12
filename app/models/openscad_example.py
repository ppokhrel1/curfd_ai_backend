from sqlalchemy import String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class OpenscadExample(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "openscad_examples"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="huggingface")
    # embedding column (vector(1536)) managed via raw SQL — pgvector type not mapped in ORM

    __table_args__ = (
        Index("ix_openscad_examples_category", "category"),
    )
