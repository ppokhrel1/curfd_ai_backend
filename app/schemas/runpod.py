from typing import Literal

from pydantic import BaseModel


class RunpodHistoryItem(BaseModel):
    role: str
    content: str


class ChatRunpodRequest(BaseModel):
    content: str | None = None
    action: Literal["process_requirements", "generate_scad", "process_scad", "health"] = "process_requirements"
    requirements_json: dict | None = None
    history: list[RunpodHistoryItem] | None = None
    sync: bool = False
    metadata_json: dict | None = None


class ChatRunpodResponse(BaseModel):
    status: str
    runpod_id: str | None = None
    message_id: str


class ImageTo3DRequest(BaseModel):
    image_url: str  # Base64 data URL or HTTPS URL
    prompt: str = ""
    output_format: Literal["glb", "stl"] = "glb"


class ImageTo3DResponse(BaseModel):
    status: str
    runpod_id: str | None = None
    message_id: str
