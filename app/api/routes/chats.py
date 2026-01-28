from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.session import Session as SessionModel
from app.schemas.chat import ChatCreate, ChatRead, ChatUpdate

router = APIRouter()


@router.post("", response_model=ChatRead, status_code=status.HTTP_201_CREATED)
def create_chat(
    payload: ChatCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    session = db.get(SessionModel, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    chat = ChatModel(session_id=payload.session_id, title=payload.title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@router.get("", response_model=list[ChatRead])
def list_chats(
    session_id: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    query = db.query(ChatModel).join(SessionModel)
    query = query.filter(SessionModel.user_id == user_id)
    if session_id:
        query = query.filter(ChatModel.session_id == session_id)
    return query.order_by(ChatModel.created_at.desc()).all()


@router.get("/{chat_id}", response_model=ChatRead)
def get_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.session and chat.session.user_id and chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return chat


@router.patch("/{chat_id}", response_model=ChatRead)
def update_chat(
    chat_id: str,
    payload: ChatUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.session and chat.session.user_id and chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if payload.title is not None:
        chat.title = payload.title
    db.commit()
    db.refresh(chat)
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.session and chat.session.user_id and chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    db.delete(chat)
    db.commit()
    return None
