from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class Asset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "assets"

    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"))
    asset_type: Mapped[str] = mapped_column(String(50))
    uri: Mapped[str] = mapped_column(Text)
    storage_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    job = relationship("Job", back_populates="assets")
    meta = relationship("AssetMeta", back_populates="asset", cascade="all, delete-orphan")
