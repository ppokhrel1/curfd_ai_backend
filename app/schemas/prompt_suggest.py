from typing import Literal

from pydantic import BaseModel


class PromptSuggestRequest(BaseModel):
    type: Literal["creative", "parametric"]
    existing_text: str | None = None


class PromptSuggestResponse(BaseModel):
    prompt: str
