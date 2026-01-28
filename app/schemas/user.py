from pydantic import BaseModel

from app.schemas.common import Timestamped


class UserCreate(BaseModel):
    username: str
    email: str | None = None
    password: str
    display_name: str | None = None


class UserRead(Timestamped):
    username: str
    email: str | None = None
    display_name: str | None = None
