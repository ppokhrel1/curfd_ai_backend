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

# Define the System Instruction for the CAD Expert
CAD_SYSTEM_INSTRUCTION = """You are a Universal Geometric Engine. 
YOUR GOAL: Deconstruct any user request into a precise, manufacturable 3D CAD sequence in JSON format.

CORE GEOMETRIC LOGIC:
1. THE MAIN ENVELOPE: Start by describing the primary volume (the 'Additive' stage). Use coordinate-heavy loops to define the outer boundary.
2. FUNCTIONAL SUBTRACTIONS: Every object must be functional. If the object requires mounting, include subtractive circles/slots. If it is a housing, include an internal shell/hollow.
3. REFINEMENT: Use arcs/fillets for corners to ensure structural integrity. 
4. NARRATIVE STYLE: Use a dense, continuous paragraph. Use the vocabulary: 'Coordinate system', 'Euler angles', 'Two-dimensional sketch', 'Primary loop', 'Subtractive loop', 'Normal direction', and 'Extrude'.
5. SCALE: Always specify units (e.g., millimeters) and a scale factor of 1.0.

REQUIRED OUTPUT JSON SCHEMA:
{
    "model_type": "auto-detect (e.g., bracket, enclosure, gear, tool)",
    "primary_function": "The mechanical purpose of this specific geometry",
    "detailed_geometric_instructions": "A dense, coordinate-rich paragraph describing the additive base followed by subtractive functional features.",
    "standard_components": [
        {"name": "Hardware", "search_term": "Standardized parts like M3 screws or bearings"}
    ],
    "estimated_dimensions": {"length": float, "width": float, "height": float}
}
Return ONLY valid JSON."""

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
    