from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.models.session import Session as SessionModel
from app.schemas.message import MessageCreate, MessageRead

router = APIRouter()


@router.post("", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = await db.get(ChatModel, payload.chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .where(ChatModel.id == payload.chat_id)
    )
    if owner and owner != user_id:
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
    return message


@router.get("", response_model=list[MessageRead])
async def list_messages(
    chat_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    stmt = (
        select(MessageModel)
        .join(ChatModel, MessageModel.chat_id == ChatModel.id)
        .join(SessionModel, ChatModel.session_id == SessionModel.id)
        .where(SessionModel.user_id == user_id)
    )
    if chat_id:
        stmt = stmt.where(MessageModel.chat_id == chat_id)
    stmt = stmt.order_by(MessageModel.created_at.asc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{message_id}", response_model=MessageRead)
async def get_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    message = await db.get(MessageModel, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .join(MessageModel, MessageModel.chat_id == ChatModel.id)
        .where(MessageModel.id == message_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return message


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    message = await db.get(MessageModel, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .join(MessageModel, MessageModel.chat_id == ChatModel.id)
        .where(MessageModel.id == message_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.delete(message)
    await db.commit()
    return None
