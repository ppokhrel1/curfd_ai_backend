from typing import Literal

from pydantic import BaseModel


class ChatRunpodRequest(BaseModel):
    content: str
    action: Literal["process_requirements", "generate_scad"] = "process_requirements"
    requirements_json: dict | None = None
    metadata_json: dict | None = None


class ChatRunpodResponse(BaseModel):
    status: str
    runpod_id: str | None = None
    message_id: str
