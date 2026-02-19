import os
import json
import traceback

from dotenv import load_dotenv
from app.core.deps import get_current_user_id_async
from google import genai
from google.genai import types

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession 

from app.helpers.gemini_helpers import get_chat_history

from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.schemas.message import MessageCreate, MessageRead

load_dotenv()

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
"""

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

router = APIRouter()
@router.post("/process_requirements", response_model=MessageRead)
async def process_requirements(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db), 
    user_id: str = Depends(get_current_user_id_async)
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
        # 2. Save User Message
        new_user_msg = MessageModel(
            chat_id=payload.chat_id,
            role="user",
            content=payload.content
        )
        db.add(new_user_msg) 
        await db.commit() 
        
        # 3. Fetch History
        raw_history = await get_chat_history(db, payload.chat_id)
        
        # Convert history to new SDK format
        # (The new SDK accepts list of dicts: [{'role': 'user', 'parts': [{'text': '...'}]}])
        formatted_contents = []
        for msg in raw_history:
             # Ensure roles are 'user' or 'model'
             formatted_contents.append(
                 types.Content(
                     role=msg['role'], 
                     parts=[types.Part.from_text(text=msg['parts'][0])]
                 )
             )
        
        # Add the NEW message to the contents list (The SDK is stateless by default)
        formatted_contents.append(
            types.Content(
                role="user", 
                parts=[types.Part.from_text(text=payload.content + "\n\nRemember: Return valid JSON...")]
            )
        )
        

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=formatted_contents,
            config=types.GenerateContentConfig(
                system_instruction=CAD_SYSTEM_INSTRUCTION,
                response_mime_type="application/json"
            )
        )
        
        ai_content = response.text

        # 5. Save AI response
        ai_msg = MessageModel(
            chat_id=payload.chat_id,
            role="assistant",
            content=ai_content,
            metadata_json=json.loads(ai_content) 
        )
        db.add(ai_msg) 
        await db.commit()
        await db.refresh(ai_msg)

        return ai_msg

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    