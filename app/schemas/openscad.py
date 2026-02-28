from typing import Optional
from pydantic import BaseModel, Field


class OpenSCADParameter(BaseModel):
    name: str
    min_val: float
    max_val: float
    default_val: float
    description: str


class OpenSCADResponse(BaseModel):
    openscad_code: str = Field(description="Complete renderable OpenSCAD code, or empty string for conversational replies.")
    parameters: list[OpenSCADParameter] = Field(description="Tunable top-level variables extracted from the code.")
    model_type: str = Field(description="Model category (e.g. 'mechanical', 'organic') or 'chat' for conversational replies.")
    message: Optional[str] = Field(default=None, description="Friendly 1-2 sentence explanation shown in chat.")
