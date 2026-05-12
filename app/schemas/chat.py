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
    # Populated by /init (single batched COUNT query). Lets the frontend
    # show real `N msgs` counts in the sidebar without having to sync
    # every chat's full message history up front. Optional so endpoints
    # that don't compute it still validate.
    message_count: int | None = None
