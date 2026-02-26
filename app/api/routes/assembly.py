import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id_async
from app.db.session import get_db
from app.models.assembly import AssemblyRead, AssemblyUpsert
from app.schemas.assembly import Assembly

router = APIRouter(prefix="/assembly", tags=["Assembly"])


@router.post("", response_model=AssemblyRead)
async def upsert_assembly(
    payload: AssemblyUpsert,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    """Create or update the assembly for a chat session."""
    now = datetime.now(timezone.utc)

    # Check for existing assembly for this chat
    stmt = select(Assembly).where(Assembly.chat_id == payload.chat_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.name = payload.name
        existing.parts = payload.parts
        existing.updated_at = now
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        new_assembly = Assembly(
            id=str(uuid.uuid4()),
            chat_id=payload.chat_id,
            name=payload.name,
            parts=payload.parts,
            created_at=now,
            updated_at=now,
        )
        db.add(new_assembly)
        await db.commit()
        await db.refresh(new_assembly)
        return new_assembly


@router.get("/{chat_id}", response_model=AssemblyRead)
async def get_assembly(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async),
):
    """Load the saved assembly for a chat session."""
    stmt = select(Assembly).where(Assembly.chat_id == chat_id)
    result = await db.execute(stmt)
    assembly = result.scalar_one_or_none()

    if not assembly:
        raise HTTPException(status_code=404, detail="No assembly found for this chat")

    return assembly
