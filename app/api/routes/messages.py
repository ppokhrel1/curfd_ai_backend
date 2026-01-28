from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.models.session import Session as SessionModel
from app.schemas.message import MessageCreate, MessageRead

router = APIRouter()


@router.post("", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
def create_message(
    payload: MessageCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    chat = db.get(ChatModel, payload.chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chat.session and chat.session.user_id and chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    message = MessageModel(
        chat_id=payload.chat_id,
        role=payload.role,
        content=payload.content,
        tokens=payload.tokens,
        metadata_json=payload.metadata_json,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get("", response_model=list[MessageRead])
def list_messages(
    chat_id: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    query = db.query(MessageModel).join(ChatModel).join(SessionModel)
    query = query.filter(SessionModel.user_id == user_id)
    if chat_id:
        query = query.filter(MessageModel.chat_id == chat_id)
    return query.order_by(MessageModel.created_at.asc()).all()


@router.get("/{message_id}", response_model=MessageRead)
def get_message(
    message_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    message = db.get(MessageModel, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.chat and message.chat.session and message.chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return message


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(
    message_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    message = db.get(MessageModel, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.chat and message.chat.session and message.chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    db.delete(message)
    db.commit()
    return None
