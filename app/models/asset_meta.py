from sqlalchemy import ForeignKey, String, Table, Column
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDMixin


asset_meta_composites = Table(
    "asset_meta_composites",
    Base.metadata,
    Column("asset_meta_id", String(36), ForeignKey("asset_meta.id"), primary_key=True),
    Column("component_asset_id", String(36), ForeignKey("assets.id"), primary_key=True),
)


class AssetMeta(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "asset_meta"

    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id"))
    part_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    component_of: Mapped[str | None] = mapped_column(String(36), ForeignKey("assets.id"), nullable=True)
    position_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_paths_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    material_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_composite_of_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    used_for_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    asset = relationship("Asset", back_populates="meta", foreign_keys=[asset_id])
    component_asset = relationship("Asset", foreign_keys=[component_of])
    composite_assets = relationship("Asset", secondary=asset_meta_composites)
