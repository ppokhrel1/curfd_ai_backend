import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
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
    chat = await db.get(ChatModel, payload.chat_id)
    if not chat:
        logger.warning(f"Chat not found: {payload.chat_id}")
        raise HTTPException(status_code=404, detail="Chat not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .where(ChatModel.id == payload.chat_id)
    )
    if owner and owner != user_id:
        logger.warning(f"User {user_id} forbidden from accessing chat {payload.chat_id}")
        raise HTTPException(status_code=403, detail="Forbidden")
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
        .order_by(MessageModel.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    if chat_id:
        owner = await db.scalar(
            select(SessionModel.user_id)
            .join(ChatModel, ChatModel.session_id == SessionModel.id)
            .where(ChatModel.id == chat_id)
        )
        chat_exists = await db.scalar(select(ChatModel.id).where(ChatModel.id == chat_id))
        if not chat_exists:
            logger.warning(f"Chat not found: {chat_id}")
            raise HTTPException(status_code=404, detail="Chat not found")
        if owner and owner != user_id:
            logger.warning(f"User {user_id} forbidden from accessing chat {chat_id}")
            raise HTTPException(status_code=403, detail="Forbidden")
        stmt = stmt.where(MessageModel.chat_id == chat_id)

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
    message = await db.get(MessageModel, message_id)
    if not message:
        logger.warning(f"Message not found: {message_id}")
        raise HTTPException(status_code=404, detail="Message not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .join(MessageModel, MessageModel.chat_id == ChatModel.id)
        .where(MessageModel.id == message_id)
    )
    if owner and owner != user_id:
        logger.warning(f"User {user_id} forbidden from accessing message {message_id}")
        raise HTTPException(status_code=403, detail="Forbidden")
    return MessageRead.model_validate(message)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    logger.info(f"User {user_id} deleting message {message_id}")
    message = await db.get(MessageModel, message_id)
    if not message:
        logger.warning(f"Message not found: {message_id}")
        raise HTTPException(status_code=404, detail="Message not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .join(MessageModel, MessageModel.chat_id == ChatModel.id)
        .where(MessageModel.id == message_id)
    )
    if owner and owner != user_id:
        logger.warning(f"User {user_id} forbidden from deleting message {message_id}")
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.delete(message)
    await db.commit()
    logger.info(f"Message {message_id} deleted by user {user_id}")
    return None
