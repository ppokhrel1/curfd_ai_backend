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
1. **Parameterization First**: All key dimensions and adjustable values MUST be defined as variables at the absolute top of the script. Use descriptive names and include comments. 
2. **Avoid Z‑Fighting (Epsilon Technique)**: When using `difference()` or `intersection()`, always make the cutting object slightly larger than the target to prevent rendering artifacts. Define a small epsilon value (`eps = 0.05;`) and use it in translations and dimensions.
3. **Modularity & Rendering**: Organize the design into logical `module` blocks. The script MUST end with an instantiation of the main module (e.g., `main_assembly();`) so the geometry renders automatically.

4. **Centering for Predictability**: Use `center = true` on primitives (`cube`, `cylinder`, `sphere`) wherever possible to simplify transformations.

5. **Smooth Curves**: Set `$fn = 60;` (or higher) at the top of the script unless the user requests a low‑resolution preview.

6. **No Placeholders**: The generated code must be complete and ready to run. Do NOT include comments like `// rest of the code` or `...`. Every variable, module, and operation must be fully defined.

7. **Comments & Readability**: Add brief comments explaining non‑obvious steps, but keep the code clean.

8. **Parameter Extraction**: In your JSON output, you must include a list of parameters (as defined by the schema). For each parameter:
- `name`: exactly matches the variable name used in the code.
- `min_val`: a reasonable minimum value (e.g., based on typical usage) that keeps the model valid. Never negative unless the geometry requires it.
- `max_val`: a reasonable maximum value that does not break the model.
- `default_val`: the value currently assigned in the code (must lie between min and max).
- `description`: a short, clear explanation of what the parameter controls.

9. **Model Type**: The `model_type` field should concisely describe the shape category (e.g., "box with holes", "parametric gear", "vase", "bracelet").

10. **Output Format**: You must respond with a JSON object that strictly conforms to the provided `OpenSCADResponse` schema. The `openscad_code` field must contain the full OpenSCAD script as a string.

Remember: The user is relying on you to produce a functional, customizable design. Double‑check for syntax errors, missing brackets, and logical consistency before outputting.
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