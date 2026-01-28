from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import Timestamped


class JobCreate(BaseModel):
    session_id: str
    prompt: str | None = None
    input_image_uri: str | None = None
    spec_json: dict | None = None
    output_format: str | None = None


class JobUpdate(BaseModel):
    status: str | None = None
    spec_json: dict | None = None
    output_format: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class JobRead(Timestamped):
    session_id: str
    status: str
    prompt: str | None = None
    input_image_uri: str | None = None
    spec_json: dict | None = None
    output_format: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
