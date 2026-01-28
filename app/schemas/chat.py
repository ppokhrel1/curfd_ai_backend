from pydantic import BaseModel

from app.schemas.common import Timestamped


class ChatCreate(BaseModel):
    session_id: str
    title: str | None = None


class ChatUpdate(BaseModel):
    title: str | None = None


class ChatRead(Timestamped):
    session_id: str
    title: str | None = None
