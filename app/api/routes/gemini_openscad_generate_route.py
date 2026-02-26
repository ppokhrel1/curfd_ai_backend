import os
import json
import traceback
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

from app.core.deps import get_current_user_id_async
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.message import Message as MessageModel
from app.schemas.message import MessageCreate, MessageRead

load_dotenv()

# ── Schema ────────────────────────────────────────────────────────────────────

class OpenSCADParameter(BaseModel):
    name: str
    min_val: float
    max_val: float
    default_val: float
    description: str

class OpenSCADResponse(BaseModel):
    openscad_code: str = Field(description="Complete renderable OpenSCAD code, or empty string for conversational replies.")
    parameters: list[OpenSCADParameter] = Field(description="Tunable top-level variables extracted from the code.")
    model_type: str = Field(description="Model category (e.g. 'mechanical', 'organic') or 'chat' for conversational replies.")
    message: Optional[str] = Field(default=None, description="Friendly 1-2 sentence explanation shown in chat.")

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an Expert OpenSCAD Engineer and CAD design assistant.

## When to generate code vs chat
- Generate a full executable OpenSCAD script when the user describes or asks to modify a 3D shape.
- Refine the existing code from history when the user says things like "make it wider", "add a hole", "change the shape".
- Answer conversationally (empty openscad_code) for general questions.

## OpenSCAD rules
1. Write the full, working script every time — no placeholders or ellipsis.
2. Coordinate system: Z = Up/Down, X = Forward/Back, Y = Left/Right.
3. No `cone()` — use `cylinder(h=..., r1=..., r2=...)`.
4. Use `eps = 0.01;` for clean boolean subtractions inside `difference()`.
5. Set `$fn = 64;` for smooth curves.
6. Put ALL dimensions as named top-level variables.
7. End the script by calling the main module (e.g. `main();`).
8. Mirror parts using `mirror([0, 1, 0])`.

## Module structure (required)
- Every distinct component = its own named module (e.g. `module frame()`, `module wheel()`).
- `main()` assembles them with `translate()` / `rotate()` / `mirror()` — no single wrapping `union()`.
- Use descriptive snake_case names. Never use `part1`, `body_combined`, `assembly`, or `combined`.
"""

# ── LangChain setup ───────────────────────────────────────────────────────────

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.getenv("GEMINI_API_KEY"),
    temperature=0.2,
)

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

# Use structured output to force the model to always return the correct schema.
# This is more reliable than manual JSON extraction.
structured_llm = llm.with_structured_output(OpenSCADResponse)
llm_chain = prompt | structured_llm

router = APIRouter()

# ── History builder ───────────────────────────────────────────────────────────

async def _build_lc_history(db: AsyncSession, chat_id: str) -> list:
    """
    Build LangChain message history from DB.
    For assistant messages, inject the actual OpenSCAD code from metadata_json
    so the model can see and refine its previous output (enables iterative design).
    """
    result = await db.execute(
        select(MessageModel)
        .where(MessageModel.chat_id == chat_id)
        .order_by(MessageModel.created_at.asc())
    )
    records = result.scalars().all()

    # Drop the most-recent user message — it's passed separately as {input}
    records = list(records)
    if records and records[-1].role == "user":
        records = records[:-1]

    lc_messages = []
    for msg in records:
        if msg.role == "user":
            lc_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            meta = msg.metadata_json
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = None

            code = meta.get("openscad_code") if isinstance(meta, dict) else None
            friendly = meta.get("message") if isinstance(meta, dict) else None

            # Show previous AI responses in JSON format so the model learns to output JSON,
            # not markdown. (Markdown history was causing the model to mimic code-fence format.)
            if code:
                ai_json = json.dumps({
                    "openscad_code": code,
                    "parameters": meta.get("parameters", []) if isinstance(meta, dict) else [],
                    "model_type": meta.get("model_type", "mechanical") if isinstance(meta, dict) else "mechanical",
                    "message": friendly or "",
                })
                lc_messages.append(AIMessage(content=ai_json))
            elif msg.content and msg.content != "Model generated.":
                ai_json = json.dumps({
                    "openscad_code": "",
                    "parameters": [],
                    "model_type": "chat",
                    "message": msg.content,
                })
                lc_messages.append(AIMessage(content=ai_json))

    return lc_messages


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/process_requirements", response_model=MessageRead)
async def gemini_openscad_generate_route(
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

    if not chat or (chat.session and user_id and chat.session.user_id != user_id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        # 1. Save user message first
        db.add(MessageModel(chat_id=payload.chat_id, role="user", content=payload.content))
        await db.commit()

        # 2. Build smart history (includes previous OpenSCAD code for iterative refinement)
        history = await _build_lc_history(db, payload.chat_id)

        # 3. Invoke LangChain chain with structured output (schema-enforced JSON).
        # The response is directly a Pydantic OpenSCADResponse object.
        response: OpenSCADResponse = await llm_chain.ainvoke({
            "history": history,
            "input": payload.content,
        })

        # 4. Extract fields (already validated by schema)
        code: str = response.openscad_code or ""
        parameters: list = [p.model_dump() for p in (response.parameters or [])]
        model_type: str = response.model_type or "chat"
        message: str = response.message or ("Model generated." if code else "Here to help!")

        # 5. Persist assistant message — store code in metadata, friendly text as content
        ai_msg = MessageModel(
            chat_id=payload.chat_id,
            role="assistant",
            content=message,
            metadata_json={
                "openscad_code": code,
                "parameters": parameters,
                "model_type": model_type,
                "message": message,
            },
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        return ai_msg

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
