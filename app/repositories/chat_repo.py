"""
Chat session repository with full CRUD and metadata support.
"""
from typing import List, Optional
from datetime import datetime
import uuid

from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message


class ChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(self, session_data: ChatSession) -> ChatSession:
        """Create a new chat session."""
        self.session.add(session_data)
        await self.session.commit()
        await self.session.refresh(session_data)
        return session_data

    async def get_session(self, session_id: uuid.UUID) -> Optional[ChatSession]:
        """Get a single chat session by ID."""
        statement = select(ChatSession).where(ChatSession.id == session_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_user_sessions(self, user_id: uuid.UUID) -> List[ChatSession]:
        """Get all chat sessions for a user, ordered by most recent."""
        statement = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
    
    async def get_session_with_metadata(self, session_id: uuid.UUID) -> Optional[dict]:
        """Get session with last message timestamp and message count."""
        session = await self.get_session(session_id)
        if not session:
            return None
        
        # Get message count
        count_stmt = select(func.count(Message.id)).where(Message.session_id == session_id)
        count_result = await self.session.execute(count_stmt)
        message_count = count_result.scalar() or 0
        
        # Get last message timestamp
        last_msg_stmt = (
            select(Message.created_at)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg_result = await self.session.execute(last_msg_stmt)
        last_message_at = last_msg_result.scalar_one_or_none()
        
        return {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": message_count,
            "last_message_at": last_message_at,
            "user_id": session.user_id,
        }
    
    async def get_user_sessions_with_metadata(self, user_id: uuid.UUID) -> List[dict]:
        """Get all user sessions with metadata."""
        sessions = await self.get_user_sessions(user_id)
        result = []
        for session in sessions:
            metadata = await self.get_session_with_metadata(session.id)
            if metadata:
                result.append(metadata)
        return result
    
    async def update_session(self, session: ChatSession) -> ChatSession:
        """Update a chat session."""
        session.updated_at = datetime.utcnow()
        self.session.add(session)
        await self.session.commit()
        await self.session.refresh(session)
        return session

    async def delete_session(self, session_id: uuid.UUID) -> bool:
        """Delete a chat session and all its messages."""
        session = await self.get_session(session_id)
        if not session:
            return False
        
        # Delete messages first
        messages = await self.get_messages(session_id)
        for msg in messages:
            await self.session.delete(msg)
        
        # Delete session
        await self.session.delete(session)
        await self.session.commit()
        return True

    async def add_message(self, message: Message) -> Message:
        """Add a message to a session."""
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def get_messages(self, session_id: uuid.UUID) -> List[Message]:
        """Get all messages for a session in chronological order."""
        statement = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
    
    async def get_message_count(self, session_id: uuid.UUID) -> int:
        """Get message count for a session."""
        statement = select(func.count(Message.id)).where(Message.session_id == session_id)
        result = await self.session.execute(statement)
        return result.scalar() or 0
