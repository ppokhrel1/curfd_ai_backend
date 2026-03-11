import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.session import Session as SessionModel
from app.schemas.chat import ChatRead
from app.schemas.session import SessionRead

logger = logging.getLogger(__name__)

router = APIRouter()


class InitResponse(BaseModel):
    sessions: list[SessionRead]
    chats: list[ChatRead]


@router.get("", response_model=InitResponse)
async def get_init(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Return all sessions and chats for the user in a single request."""
    # 1. All sessions for this user
    session_result = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == user_id)
        .order_by(SessionModel.created_at.desc())
    )
    sessions = session_result.scalars().all()

    if not sessions:
        return InitResponse(sessions=[], chats=[])

    # 2. All chats across all sessions in one query
    session_ids = [s.id for s in sessions]
    chat_result = await db.execute(
        select(ChatModel)
        .where(ChatModel.session_id.in_(session_ids))
        .order_by(ChatModel.updated_at.desc())
    )
    chats = chat_result.scalars().all()

    return InitResponse(
        sessions=[SessionRead.model_validate(s) for s in sessions],
        chats=[ChatRead.model_validate(c) for c in chats],
    )
