from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.message import Message as MessageModel

async def get_chat_history(db: AsyncSession, chat_id: str):
    # 1. Use 'select' instead of 'db.query'
    stmt = (
        select(MessageModel)
        .where(MessageModel.chat_id == chat_id)
        .order_by(MessageModel.created_at.asc())
    )
    
    # 2. Await the execution
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    # 3. Format for Gemini
    history = []
    for msg in messages:
        # Map 'assistant' role to 'model' for Gemini
        role = "model" if msg.role == "assistant" else "user"
        history.append({"role": role, "parts": [msg.content]})
    
    return history