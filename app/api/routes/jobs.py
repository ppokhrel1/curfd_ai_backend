from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.job import Job as JobModel
from app.schemas.job import JobCreate, JobRead, JobUpdate
from app.services.pipeline import build_default_spec

router = APIRouter()


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    job = JobModel(
        session_id=payload.session_id,
        prompt=payload.prompt,
        input_image_uri=payload.input_image_uri,
        spec_json=payload.spec_json or build_default_spec(payload.prompt),
        output_format=payload.output_format,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("", response_model=list[JobRead])
def list_jobs(session_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(JobModel)
    if session_id:
        query = query.filter(JobModel.session_id == session_id)
    return query.order_by(JobModel.created_at.desc()).all()


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/{job_id}", response_model=JobRead)
def update_job(job_id: str, payload: JobUpdate, db: Session = Depends(get_db)):
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

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

    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/start", response_model=JobRead)
def start_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/complete", response_model=JobRead)
def complete_job(job_id: str, success: bool = True, db: Session = Depends(get_db)):
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "succeeded" if success else "failed"
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job
