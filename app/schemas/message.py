import json
from pydantic import BaseModel, field_validator

from app.schemas.common import Timestamped


class ImageData(BaseModel):
    data: str  # base64-encoded image data
    media_type: str = "image/jpeg"  # image/jpeg, image/png, image/webp, image/gif


class MessageCreate(BaseModel):
    chat_id: str
    role: str
    content: str
    tokens: int | None = None
    metadata_json: dict | None = None
    openscad_code: str | dict | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_thinking: bool = False
    images: list[ImageData] | None = None

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