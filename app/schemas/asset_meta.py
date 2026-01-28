from pydantic import BaseModel

from app.schemas.common import Timestamped


class AssetMetaCreate(BaseModel):
    asset_id: str
    part_name: str | None = None
    component_of: str | None = None
    position_json: dict | None = None
    image_paths_json: list | None = None
    material_json: dict | None = None
    is_composite_of: list[str] | None = None
    used_for_json: list | None = None


class AssetMetaUpdate(BaseModel):
    part_name: str | None = None
    component_of: str | None = None
    position_json: dict | None = None
    image_paths_json: list | None = None
    material_json: dict | None = None
    is_composite_of: list[str] | None = None
    used_for_json: list | None = None


class AssetMetaRead(Timestamped):
    asset_id: str
    part_name: str | None = None
    component_of: str | None = None
    position_json: dict | None = None
    image_paths_json: list | None = None
    uploaded_by: str | None = None
    material_json: dict | None = None
    is_composite_of: list[str] | None = None
    used_for_json: list | None = None
