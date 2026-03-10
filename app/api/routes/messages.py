import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.core.ownership import get_chat_verified, get_message_verified
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.models.session import Session as SessionModel
from app.schemas.message import MessageCreate, MessageRead

router = APIRouter()
logger = logging.getLogger(__name__)
QUERY_TIMEOUT_SECONDS = 10.0


@router.post("", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    logger.info(f"User {user_id} creating message for chat {payload.chat_id}")
    # Single query: verify chat exists + ownership
    await get_chat_verified(db, payload.chat_id, user_id)
    message = MessageModel(
        chat_id=payload.chat_id,
        role=payload.role,
        content=payload.content,
        tokens=payload.tokens,
        metadata_json=payload.metadata_json,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    logger.info(f"Message {message.id} created for chat {payload.chat_id}")
    return MessageRead.model_validate(message)


@router.get("", response_model=list[MessageRead])
async def list_messages(
    chat_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    logger.info(f"User {user_id} listing messages for chat {chat_id}")
    stmt = (
        select(MessageModel)
        .join(ChatModel, MessageModel.chat_id == ChatModel.id)
        .join(SessionModel, ChatModel.session_id == SessionModel.id)
        .where(SessionModel.user_id == user_id)
    )
    if chat_id:
        stmt = stmt.where(MessageModel.chat_id == chat_id)
    stmt = stmt.order_by(MessageModel.created_at.asc()).offset(offset).limit(limit)

    try:
        result = await asyncio.wait_for(db.execute(stmt), timeout=QUERY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        logger.error(f"Message query timed out for user {user_id}, chat {chat_id}")
        raise HTTPException(status_code=504, detail="Message query timed out") from exc

    messages = result.scalars().all()
    return [MessageRead.model_validate(message) for message in messages]


@router.get("/{message_id}", response_model=MessageRead)
async def get_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    logger.info(f"User {user_id} getting message {message_id}")
    message = await get_message_verified(db, message_id, user_id)
    return MessageRead.model_validate(message)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    logger.info(f"User {user_id} deleting message {message_id}")
    message = await get_message_verified(db, message_id, user_id)
    await db.delete(message)
    await db.commit()
    logger.info(f"Message {message_id} deleted by user {user_id}")
    return None
