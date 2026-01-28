"""
Chat Session API Routes

Implements:
- POST /chat/sessions - Create session (Req 2.1)
- GET /chat/sessions - List user sessions (Req 2.4)
- GET /chat/sessions/{id} - Get session details
- DELETE /chat/sessions/{id} - Delete session (Req 2.5)
- POST /chat/sessions/{id}/messages - Send message (Req 2.2)
- GET /chat/sessions/{id}/messages - Get history (Req 2.3)
"""
from typing import List, Annotated
from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import get_current_user
from app.db.database import get_session
from app.models.user import User
from app.models.chat import Message
from app.repositories.chat_repo import ChatRepository
from app.repositories.model_repo import ModelRepository
from app.services.chat_service import ChatService
from app.services.model_service import ModelService
from app.services.ml_client import ml_client

router = APIRouter()


class CreateSessionRequest(BaseModel):
    title: str | None = None


class SessionResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: str
    updated_at: str
    message_count: int
    last_message_at: str | None

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    trigger_generation: bool = False


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: str

    class Config:
        from_attributes = True


class GeneratedModelResponse(BaseModel):
    """Model data returned when a 3D model is generated."""
    asset_id: str | None = None
    sdf_url: str
    yaml_url: str | None = None
    assets: list = []  # List of {filename: str, url: str} objects
    model_name: str
    model_type: str = "custom"
    description: str = ""
    parts: list = []
    joints: list = []
    parameters: dict = {}
    requirements: dict = {}
    metrics: dict = {}


class SendMessageResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
    generated_model: GeneratedModelResponse | None = None


def _format_session(session_data: dict) -> SessionResponse:
    """Format session data for API response."""
    return SessionResponse(
        id=session_data["id"],
        title=session_data["title"] or "Untitled Chat",
        created_at=session_data["created_at"].isoformat(),
        updated_at=session_data.get("updated_at", session_data["created_at"]).isoformat() if session_data.get("updated_at") else session_data["created_at"].isoformat(),
        message_count=session_data["message_count"],
        last_message_at=session_data["last_message_at"].isoformat() if session_data.get("last_message_at") else None
    )


def _format_message(msg: Message) -> MessageResponse:
    """Format message for API response."""
    return MessageResponse(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        created_at=msg.created_at.isoformat()
    )


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: CreateSessionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """Create a new chat session for the authenticated user."""
    chat_repo = ChatRepository(session)
    chat_service = ChatService(chat_repo)
    
    chat_session = await chat_service.create_session(current_user.id, request.title)
    session_data = await chat_service.get_session(chat_session.id)
    
    return _format_session(session_data)


@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """List all chat sessions for the authenticated user."""
    chat_repo = ChatRepository(session)
    chat_service = ChatService(chat_repo)
    
    sessions = await chat_service.get_user_sessions(current_user.id)
    return [_format_session(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_details(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """Get details of a specific chat session."""
    chat_repo = ChatRepository(session)
    chat_service = ChatService(chat_repo)
    
    session_data = await chat_service.get_session(session_id)
    if not session_data or session_data["user_id"] != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    return _format_session(session_data)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """Delete a chat session and all its messages."""
    chat_repo = ChatRepository(session)
    chat_service = ChatService(chat_repo)
    
    deleted = await chat_service.delete_session(session_id, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: uuid.UUID,
    request: CreateSessionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """Update chat session details (e.g., title)."""
    chat_repo = ChatRepository(session)
    chat_service = ChatService(chat_repo)
    
    # Verify ownership and get session
    db_session = await chat_repo.get_session(session_id)
    if not db_session or db_session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    # Update fields
    if request.title is not None:
        db_session.title = request.title
        await chat_repo.update_session(db_session)
    
    # Get updated session with metadata
    session_data = await chat_service.get_session(session_id)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session data not found after update"
        )
        
    return _format_session(session_data)


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(
    session_id: uuid.UUID,
    request: SendMessageRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """
    Send a message to chat session and get AI response.
    
    The message is forwarded to ML service for response generation.
    Both user message and AI response are stored and returned.
    If a 3D model is generated, includes model data with SDF URLs.
    """
    chat_repo = ChatRepository(session)
    model_repo = ModelRepository(session)
    model_service = ModelService(model_repo, ml_client, chat_repo)
    chat_service = ChatService(chat_repo, ml_client, model_service, model_repo)
    
    try:
        result = await chat_service.send_message(
            session_id,
            request.content,
            current_user.id
        )
        
        response = SendMessageResponse(
            user_message=_format_message(result["user_message"]),
            assistant_message=_format_message(result["assistant_message"])
        )
        
        # Include generated model data if available
        if result.get("generated_model"):
            response.generated_model = GeneratedModelResponse(**result["generated_model"])
        
        return response
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.get("/sessions/{session_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """Get all messages for a chat session in chronological order."""
    chat_repo = ChatRepository(session)
    
    # Verify ownership
    session_data = await chat_repo.get_session(session_id)
    if not session_data or session_data.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    messages = await chat_repo.get_messages(session_id)
    return [_format_message(msg) for msg in messages]
