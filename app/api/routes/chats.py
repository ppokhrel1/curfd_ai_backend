from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.session import Session as SessionModel
from app.schemas.chat import ChatCreate, ChatRead, ChatUpdate

router = APIRouter()


async def _validate_and_claim_session_owner(
    *,
    db: AsyncSession,
    session_id: str,
    user_id: str,
) -> SessionModel:
    session = await db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        # Migration fallback:
        # reassign legacy session ownership to current Supabase user.
        session.user_id = user_id
        await db.commit()
        await db.refresh(session)
    if session.user_id is None:
        session.user_id = user_id
        await db.commit()
        await db.refresh(session)
    return session


@router.post("", response_model=ChatRead, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _validate_and_claim_session_owner(
        db=db, session_id=payload.session_id, user_id=user_id
    )
    chat = ChatModel(session_id=payload.session_id, title=payload.title)
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return chat


@router.get("", response_model=list[ChatRead])
async def list_chats(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    await _validate_and_claim_session_owner(
        db=db, session_id=session_id, user_id=user_id
    )
    result = await db.execute(
        select(ChatModel)
        .where(ChatModel.session_id == session_id)
        .order_by(ChatModel.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{chat_id}", response_model=ChatRead)
async def get_chat(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = await db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .where(ChatModel.id == chat_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return chat


@router.patch("/{chat_id}", response_model=ChatRead)
async def update_chat(
    chat_id: str,
    payload: ChatUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = await db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .where(ChatModel.id == chat_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if payload.title is not None:
        chat.title = payload.title
    await db.commit()
    await db.refresh(chat)
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = await db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(ChatModel, ChatModel.session_id == SessionModel.id)
        .where(ChatModel.id == chat_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.delete(chat)
    await db.commit()
    return None
