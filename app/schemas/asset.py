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
