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
    image_url: str | None = None  # Base64 data URL or HTTPS URL; optional when prompt is provided
    prompt: str = ""
    output_format: Literal["glb", "stl"] = "glb"
    # When true the worker skips Hunyuan3D-Part decomposition and returns
    # mesh-only (~5-10s faster). Default: produce parts.
    skip_segmentation: bool = False
    # When true the worker also runs Hunyuan3D-Paint to apply UV-mapped
    # textures to the mesh — adds ~30-90s per request and ~12-16 GB VRAM.
    with_texture: bool = False


class ImageTo3DResponse(BaseModel):
    status: str
    runpod_id: str | None = None
    message_id: str
