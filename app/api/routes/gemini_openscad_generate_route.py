import os
import json
import traceback
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession 

from app.core.deps import get_current_user_id_async
from app.helpers.gemini_helpers import get_chat_history
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.schemas.message import MessageCreate, MessageRead
from json_repair import repair_json 

load_dotenv()

OPENSCAD_SYSTEM_INSTRUCTION = """You are an Expert OpenSCAD Engineer. 
YOUR GOAL: Generate highly robust, 3D-printable, parameterizable, and syntactically correct OpenSCAD code.

CRITICAL RULES FOR OPENSCAD:
1. AVOID Z-FIGHTING (CRITICAL): When using `difference()` or `intersection()`, cutting tools MUST be slightly larger than the object being cut to prevent 2D artifacts and non-manifold edges. Always define an epsilon (e.g., `eps = 0.05;`) and add it to cutting dimensions and translations (e.g., `translate([0, 0, -eps]) cylinder(h=height+2*eps, r=hole_radius);`).
2. PARAMETERIZATION: ALL key dimensions must be defined as variables at the absolute top of the script. Do NOT hardcode dimensions inside modules.
3. ALIGNMENT: Use `center = true` on primitives (`cube`, `cylinder`) wherever possible to make mathematical transformations predictable.
4. MODULARITY & RENDERING: Group logical parts into `module` blocks. The script MUST end with a module instantiation (e.g., `main_assembly();`) so the geometry actually renders.
5. SMOOTH CURVES: Set `$fn = 60;` (or higher) at the top of the file.
6. COMPLETENESS: Write the ENTIRE script. DO NOT use placeholders like `// rest of the code`.
"""

class OpenSCADParameter(BaseModel):
    name: str
    min_val: float
    max_val: float
    default_val: float
    description: str

class OpenSCADResponse(BaseModel):
    openscad_code: str = Field(description="The complete, renderable OpenSCAD code.")
    parameters: list[OpenSCADParameter] = Field(description="Tunable variables from the code.")
    model_type: str = Field(description="Model category.")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
router = APIRouter()

@router.post("/process_requirements", response_model=MessageRead)
async def gemini_openscad_generate_route(
    payload: MessageCreate,
    db: AsyncSession = Depends(get_db), 
    user_id: str = Depends(get_current_user_id_async)
):
    # Auth & Chat Lookup
    stmt = select(ChatModel).options(selectinload(ChatModel.session)).where(ChatModel.id == payload.chat_id)
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()

    if not chat or (chat.session and user_id and chat.session.user_id != user_id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        # 1. Save User Message
        db.add(MessageModel(chat_id=payload.chat_id, role="user", content=payload.content))
        await db.commit() 
        
        # 2. Build History (Role Mapping: assistant -> model)
        raw_history = await get_chat_history(db, payload.chat_id)
        formatted_contents = [
            types.Content(role="model" if m['role'] == "assistant" else "user", 
                          parts=[types.Part.from_text(text=m['parts'][0])])
            for m in raw_history
        ]
        formatted_contents.append(types.Content(role="user", parts=[types.Part.from_text(text=payload.content)]))
        
        # 3. Request Generation
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=formatted_contents,
            config=types.GenerateContentConfig(
                system_instruction=OPENSCAD_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=OpenSCADResponse,
            )
        )
        print("Raw Gemini Response:", response.text)  # Debugging
        # 4. Parse & Save
        data = json.loads(repair_json(response.text))
        ai_msg = MessageModel(
            chat_id=payload.chat_id,
            role="assistant", 
            content="Model generated.", # Keep history light
            metadata_json=data 
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        return ai_msg

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))