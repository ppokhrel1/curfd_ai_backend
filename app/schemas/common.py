from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Timestamped(ORMBase):
    id: str
    created_at: datetime
    updated_at: datetime
