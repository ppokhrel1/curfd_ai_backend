import json
import traceback

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id_async
from app.services.openscad_agent.llm_provider import get_llm
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.schemas.message import MessageCreate, MessageRead

CAD_SYSTEM_INSTRUCTION = """You are a CadQuery Expert.
YOUR GOAL: Describe the geometry in a way that maps directly to CadQuery operations (Workplanes, extrusions, cuts, unions).

GUIDELINES:
1. COMPONENT BREAKDOWN: Describe the object as a set of simple parts (Base, Main Body, Attachments).
2. GEOMETRIC LOGIC: Instead of specific coordinates (like "at 50,175,50"), use relative positions (like "centered on the top face of the base", "extending from the side").
3. PARAMETERS: Mention key dimensions (width, height) but allow the coder to define them as variables.
4. FORMAT: Keep the description clear and step-by-step.

REQUIRED OUTPUT JSON SCHEMA:
{
    "model_type": "...",
    "primary_function": "...",
    "detailed_geometric_instructions": "Create a function that generates a microscope. 1. Define a base_width=150 and base_depth=200. Create a box of these dimensions. 2. On the top face, towards the back, create a pillar of height 250. 3. From the top of the pillar, loft or sweep an arm that hangs over the center...",
    ...
}

IMPORTANT: Return ONLY valid JSON. No markdown, no code fences."""

_llm = get_llm()

router = APIRouter()


@router.post("/process_requirements", response_model=MessageRead)
async def process_requirements(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    stmt = (
        select(ChatModel)
        .options(selectinload(ChatModel.session))
        .where(ChatModel.id == payload.chat_id)
    )
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()

    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if chat.session and user_id and chat.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        # Save user message
        new_user_msg = MessageModel(
            chat_id=payload.chat_id,
            role="user",
            content=payload.content,
        )
        db.add(new_user_msg)
        await db.commit()

        # Build LangChain message history
        history_stmt = (
            select(MessageModel)
            .where(MessageModel.chat_id == payload.chat_id)
            .order_by(MessageModel.created_at.asc())
        )
        history_result = await db.execute(history_stmt)
        db_messages = history_result.scalars().all()

        messages = [SystemMessage(content=CAD_SYSTEM_INSTRUCTION)]
        for msg in db_messages:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            else:
                messages.append(AIMessage(content=msg.content))

        # Call LLM (uses whatever provider is configured)
        response = await _llm.ainvoke(messages)
        ai_content = response.content

        # Try to parse as JSON for metadata
        try:
            metadata = json.loads(ai_content)
        except (json.JSONDecodeError, TypeError):
            metadata = None

        # Save AI response
        ai_msg = MessageModel(
            chat_id=payload.chat_id,
            role="assistant",
            content=ai_content,
            metadata_json=metadata,
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        return ai_msg

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
