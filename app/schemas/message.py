from pydantic import BaseModel

from app.schemas.common import Timestamped


class MessageCreate(BaseModel):
    chat_id: str
    role: str
    content: str
    tokens: int | None = None
    metadata_json: dict | None = None


class MessageRead(Timestamped):
    chat_id: str
    role: str
    content: str
    tokens: int | None = None
    metadata_json: dict | None = None
