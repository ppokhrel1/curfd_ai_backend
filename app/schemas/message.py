import json
from pydantic import BaseModel, field_validator

from app.schemas.common import Timestamped


class MessageCreate(BaseModel):
    chat_id: str
    role: str
    content: str
    tokens: int | None = None
    metadata_json: dict | None = None
    openscad_code: str | dict | None = None

    @field_validator('metadata_json', mode='before')
    @classmethod
    def parse_metadata(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v


class MessageRead(Timestamped):
    chat_id: str
    role: str
    content: str
    tokens: int | None = None
    metadata_json: dict | None = None
    openscad_code: str | dict | None = None

    @field_validator('metadata_json', mode='before')
    @classmethod
    def parse_metadata(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v