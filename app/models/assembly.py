from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel


class AssemblyUpsert(BaseModel):
    chat_id: str
    name: str = "My Assembly"
    # Opaque list — each item is a serialised AssemblyPart from the frontend
    parts: List[Any] = []


class AssemblyRead(BaseModel):
    id: str
    chat_id: str
    name: str
    parts: List[Any]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
