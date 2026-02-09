from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.job import Job as JobModel
from app.models.session import Session as SessionModel
from app.schemas.job import JobCreate, JobRead, JobUpdate
from app.services.pipeline import build_default_spec

router = APIRouter()


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    session = await db.get(SessionModel, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    job = JobModel(
        session_id=payload.session_id,
        prompt=payload.prompt,
        input_image_uri=payload.input_image_uri,
        spec_json=payload.spec_json or build_default_spec(payload.prompt),
        output_format=payload.output_format,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("", response_model=list[JobRead])
async def list_jobs(
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    stmt = (
        select(JobModel)
        .join(SessionModel, JobModel.session_id == SessionModel.id)
        .where(SessionModel.user_id == user_id)
    )
    if session_id:
        stmt = stmt.where(JobModel.session_id == session_id)
    stmt = stmt.order_by(JobModel.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    job = await db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .where(JobModel.id == job_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return job


@router.patch("/{job_id}", response_model=JobRead)
async def update_job(
    job_id: str,
    payload: JobUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    job = await db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .where(JobModel.id == job_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if payload.status is not None:
        job.status = payload.status
    if payload.spec_json is not None:
        job.spec_json = payload.spec_json
    if payload.output_format is not None:
        job.output_format = payload.output_format
    if payload.started_at is not None:
        job.started_at = payload.started_at
    if payload.finished_at is not None:
        job.finished_at = payload.finished_at
    if payload.error is not None:
        job.error = payload.error

    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/start", response_model=JobRead)
async def start_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    job = await db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .where(JobModel.id == job_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/complete", response_model=JobRead)
async def complete_job(
    job_id: str,
    success: bool = True,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    job = await db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .where(JobModel.id == job_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    job.status = "succeeded" if success else "failed"
    job.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return job
