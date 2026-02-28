from pydantic import BaseModel

from app.schemas.common import Timestamped


class AssetCreate(BaseModel):
    job_id: str
    asset_type: str
    uri: str
    storage_provider: str | None = None
    metadata_json: dict | None = None


class AssetRead(Timestamped):
    job_id: str
    asset_type: str
    uri: str
    storage_provider: str | None = None
    metadata_json: dict | None = None


class AssetSearchResult(Timestamped):
    """Asset with embedded part metadata for search results."""
    job_id: str
    asset_type: str
    uri: str
    storage_provider: str | None = None
    metadata_json: dict | None = None
    part_name: str | None = None
    component_of: str | None = None
    position_json: dict | None = None
    material_json: dict | None = None
