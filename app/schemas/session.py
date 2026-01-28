from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import Timestamped


class SessionCreate(BaseModel):
    user_id: str | None = None
    status: str | None = None
    metadata_json: dict | None = None


class SessionUpdate(BaseModel):
    status: str | None = None
    last_active_at: datetime | None = None
    metadata_json: dict | None = None


class SessionRead(Timestamped):
    user_id: str | None = None
    status: str
    last_active_at: datetime
    metadata_json: dict | None = None
