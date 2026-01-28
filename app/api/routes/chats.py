from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.schemas.chat import ChatCreate, ChatRead, ChatUpdate

router = APIRouter()


@router.post("", response_model=ChatRead, status_code=status.HTTP_201_CREATED)
def create_chat(payload: ChatCreate, db: Session = Depends(get_db)):
    chat = ChatModel(session_id=payload.session_id, title=payload.title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


@router.get("", response_model=list[ChatRead])
def list_chats(session_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ChatModel)
    if session_id:
        query = query.filter(ChatModel.session_id == session_id)
    return query.order_by(ChatModel.created_at.desc()).all()


@router.get("/{chat_id}", response_model=ChatRead)
def get_chat(chat_id: str, db: Session = Depends(get_db)):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.patch("/{chat_id}", response_model=ChatRead)
def update_chat(chat_id: str, payload: ChatUpdate, db: Session = Depends(get_db)):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if payload.title is not None:
        chat.title = payload.title
    db.commit()
    db.refresh(chat)
    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(chat_id: str, db: Session = Depends(get_db)):
    chat = db.get(ChatModel, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    db.delete(chat)
    db.commit()
    return None
