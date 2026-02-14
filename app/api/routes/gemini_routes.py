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
CAD_SYSTEM_INSTRUCTION = """You are a Geometric Data Preprocessor acting as a bridge to a fine-tuned CAD-Query code generation model.
YOUR GOAL: Analyze the user's request and output a JSON object containing a highly specific, mathematically explicit text description of the geometry.

CRITICAL INSTRUCTION FOR 'detailed_geometric_instructions':
You must generate a dense, continuous paragraph of procedural geometry exactly matching the style of the 'CAD-Coder' dataset. Do not use bullet points or high-level summaries. Describe the geometry mathematically using the following strict narrative flow and vocabulary:

1. Coordinate System: Always begin with: "Start by creating a new coordinate system with Euler angles set to [X], [Y], and [Z] degrees, and a translation vector of [X], [Y], [Z]."
2. 2D Sketching: Describe faces and loops explicitly. Example: "Next, draw a two-dimensional sketch on the first face. In the first loop, draw a circle centered at coordinates (X, Y) with a radius of R." 
3. Lines and Arcs: For complex shapes, list exact coordinates continuously. Example: "...start by drawing a line from (X1, Y1) to (X2, Y2), followed by an arc from (X2, Y2) to (X3, Y3) with a midpoint at (MX, MY)."
4. Scaling: Always include a scale instruction. Example: "Apply a scale factor of [factor] to the entire two-dimensional sketch."
5. Transformation & Extrusion: Use this exact phrasing: "To transform the two-dimensional sketch into a three-dimensional object, extrude the sketch along the normal direction by [distance] units. Ensure that the extrusion does not occur in the opposite direction of the normal."
6. Bounding Box: Always conclude with exact dimensions: "This process will generate a new solid body with the following dimensions: length of [L] units, width of [W] units, and height of [H] units."

REQUIRED OUTPUT JSON SCHEMA:
{
    "model_type": "drone|robot|car|custom",
    "primary_function": "Short summary of purpose",
    "detailed_geometric_instructions": "The continuous, dense, coordinate-heavy mathematical paragraph as defined above.",
    "standard_components": [
        {"name": "Part Name", "search_term": "search keywords"}
    ],
    "estimated_dimensions": {"length": float, "width": float, "height": float}
}
Return ONLY valid JSON.
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
    