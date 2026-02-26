from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from app.models.scad_version import ScadVersion

router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ScadVersionCreate(BaseModel):
    chat_id: str
    code: str
    label: Optional[str] = None


class ScadVersionRead(BaseModel):
    id: str
    chat_id: str
    version_number: int
    label: Optional[str] = None
    code: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ScadVersionSummary(BaseModel):
    """Lightweight list item — code is omitted to keep the response small."""
    id: str
    chat_id: str
    version_number: int
    label: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=ScadVersionRead, status_code=201)
async def create_version(
    payload: ScadVersionCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Save the current SCAD code as a new numbered version for the given chat."""
    # Verify the chat belongs to the requesting user
    chat = await db.get(ChatModel, payload.chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Determine the next version number for this chat
    result = await db.execute(
        select(func.max(ScadVersion.version_number)).where(
            ScadVersion.chat_id == payload.chat_id
        )
    )
    max_ver = result.scalar() or 0
    next_ver = max_ver + 1

    now = datetime.now(timezone.utc)
    version = ScadVersion(
        chat_id=payload.chat_id,
        version_number=next_ver,
        label=payload.label or f"v{next_ver}",
        code=payload.code,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return version


@router.get("/{chat_id}", response_model=list[ScadVersionSummary])
async def list_versions(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """List all versions for a chat (newest first, code excluded)."""
    result = await db.execute(
        select(ScadVersion)
        .where(ScadVersion.chat_id == chat_id)
        .order_by(ScadVersion.version_number.desc())
    )
    return result.scalars().all()


@router.get("/{chat_id}/{version_id}", response_model=ScadVersionRead)
async def get_version(
    chat_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Retrieve the full code for a specific version."""
    version = await db.get(ScadVersion, version_id)
    if not version or version.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Version not found")
    return version
