import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

# Strip ```openscad ... ``` fences AND bare language tag left at the start
_FENCE_RE = re.compile(r"^```[\w]*\n?|```\s*$", re.MULTILINE)
_LANG_TAG_RE = re.compile(r"^\s*openscad\s*\n", re.IGNORECASE)


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

    @field_validator("openscad_code", mode="before")
    @classmethod
    def strip_markdown_fences(cls, v: str) -> str:
        if not v:
            return v
        v = _FENCE_RE.sub("", v)
        v = _LANG_TAG_RE.sub("", v)
        return v.strip()
