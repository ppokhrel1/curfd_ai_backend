from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.session import Session as SessionModel
from app.schemas.session import SessionCreate, SessionRead, SessionUpdate

router = APIRouter()


@router.post("/", response_model=SessionRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    if payload.user_id and payload.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    session = SessionModel(
        user_id=user_id,
        name=payload.name,
        status=payload.status or "active",
        metadata_json=payload.metadata_json,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/", response_model=list[SessionRead], include_in_schema=False)
@router.get("", response_model=list[SessionRead])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == user_id)
        .order_by(SessionModel.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{session_id}/", response_model=SessionRead, include_in_schema=False)
@router.get("/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    session = await db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return session


@router.patch("/{session_id}/", response_model=SessionRead, include_in_schema=False)
@router.patch("/{session_id}", response_model=SessionRead)
async def update_session(
    session_id: str,
    payload: SessionUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    session = await db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if payload.name is not None:
        session.name = payload.name
    if payload.status is not None:
        session.status = payload.status
    if payload.last_active_at is not None:
        session.last_active_at = payload.last_active_at
    if payload.metadata_json is not None:
        session.metadata_json = payload.metadata_json

    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/{session_id}/", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    session = await db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.delete(session)
    await db.commit()
    return None
