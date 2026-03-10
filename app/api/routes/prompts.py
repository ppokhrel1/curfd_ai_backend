import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.deps import get_current_user_id_async
from app.schemas.prompt_suggest import PromptSuggestRequest, PromptSuggestResponse
from app.services.prompt_suggest import suggest_prompt

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/suggest", response_model=PromptSuggestResponse)
async def suggest_prompt_route(
    payload: PromptSuggestRequest,
    user_id: str = Depends(get_current_user_id_async),
):
    """Generate or enhance a 3D modeling prompt using the configured LLM."""
    try:
        result = await suggest_prompt(
            prompt_type=payload.type,
            existing_text=payload.existing_text,
        )
        return PromptSuggestResponse(prompt=result)
    except Exception as e:
        logger.error(f"Prompt suggestion failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
