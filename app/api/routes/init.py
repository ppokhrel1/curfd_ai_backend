import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
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

    # 3. Per-chat message count in a single GROUP BY so the sidebar
    # can render real `N msgs` totals without each chat having to
    # full-sync its message history on click. Single roundtrip,
    # indexed via the existing (chat_id, created_at) composite.
    chat_ids = [c.id for c in chats]
    counts: dict[str, int] = {}
    if chat_ids:
        count_result = await db.execute(
            select(MessageModel.chat_id, func.count(MessageModel.id))
            .where(MessageModel.chat_id.in_(chat_ids))
            .group_by(MessageModel.chat_id)
        )
        counts = {chat_id: cnt for chat_id, cnt in count_result.all()}

    chat_reads: list[ChatRead] = []
    for c in chats:
        cr = ChatRead.model_validate(c)
        cr.message_count = counts.get(c.id, 0)
        chat_reads.append(cr)

    return InitResponse(
        sessions=[SessionRead.model_validate(s) for s in sessions],
        chats=chat_reads,
    )
