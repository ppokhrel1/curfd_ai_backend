import os
import uuid
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import json

from app.core.deps import get_current_user_id_async
from app.db.session import get_db
from app.models.chat import Chat as ChatModel
from pydantic import BaseModel
from typing import Optional, Dict

# 🚨 1. Import Celery
from celery import Celery

from app.models.scad_optimization import ScadJobCreate, ScadJobRead
from app.schemas.scad_job import ScadJob

router = APIRouter(prefix="/optimization", tags=["OpenSCAD Optimization"])

# 🚨 2. Initialize a Celery client connected to your Redis container
WORKER_BACKEND_URL = os.getenv("WORKER_BACKEND_URL", "http://165.232.60.146")

@router.post("/start", response_model=ScadJobRead)
async def start_optimization_job(
    payload: ScadJobCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async)
):
    stmt = select(ChatModel).where(ChatModel.id == payload.chat_id)
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()
    print(f"User {user_id} is starting optimization job for chat {payload}. Chat found: {bool(chat)}")    
    if not chat:
        raise HTTPException(status_code=403, detail="Unauthorized or Chat not found")
 
    now = datetime.now(timezone.utc)
    job_id = str(uuid.uuid4())
    new_job = ScadJob(
        id=job_id,
        chat_id=payload.chat_id,
        status="Pending",
        openscad_code=payload.openscad_code,
        parameters=[p.model_dump() for p in payload.parameters],
        generations=payload.generations,
        population_size=payload.population_size,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(new_job)
    await db.commit()

    # Construct Webhook URL
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
    api_prefix = os.getenv("API_V1_PREFIX", "/api/v1")
    webhook_target = f"{backend_url}{api_prefix}/scad-genetic-algo/optimization/webhook/{job_id}"

    try:
        # Send HTTP request to Droplet
        async with httpx.AsyncClient(timeout=10.0) as client:
            worker_payload = {
                "openscad_code": payload.openscad_code,
                "parameters": [p.model_dump() for p in payload.parameters],
                "generations": payload.generations,
                "population_size": payload.population_size,
                "webhook_url": webhook_target
            }
            response = await client.post(f"{WORKER_BACKEND_URL}/optimize/custom", json=worker_payload)
            response.raise_for_status()
            worker_data = response.json()

            new_job.worker_task_id = worker_data.get("task_id")
            new_job.status = "Processing"
            await db.commit()
            await db.refresh(new_job)
            
            return new_job

    except httpx.RequestError as e:
        traceback.print_exc()
        new_job.status = "Failed"
        new_job.error = str(e)
        await db.commit()
        raise HTTPException(status_code=503, detail="Worker unavailable")
    except Exception as e:
        traceback.print_exc()
        new_job.status = "Failed"
        new_job.error = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail="Failed to send job to worker.")


class WorkerWebhookPayload(BaseModel):
    status: str
    task_id: str
    optimized_parameters: Optional[Dict[str, float]] = None
    fitness_score: Optional[float] = None
    result_url: Optional[str] = None
    error: Optional[str] = None

@router.post("/webhook/{job_id}")
async def worker_webhook(job_id: str, payload: WorkerWebhookPayload, db: AsyncSession = Depends(get_db)):
    stmt = select(ScadJob).where(ScadJob.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        return {"status": "ignored", "detail": "Job not found"}

    job.status = payload.status
    job.finished_at = datetime.now(timezone.utc)
    
    if payload.status.startswith("Completed"):
        job.optimized_parameters = payload.optimized_parameters
        job.fitness_score = payload.fitness_score
        job.result_url = payload.result_url
    elif payload.status == "Failed":
        job.error = payload.error

    await db.commit()
    return {"status": "success"}

@router.get("/list/{chat_id}", response_model=list[ScadJobRead])
async def list_optimization_jobs(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id_async)
):
    """Return all optimization jobs for a given chat, newest first."""
    stmt = (
        select(ScadJob)
        .where(ScadJob.chat_id == chat_id)
        .order_by(ScadJob.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/stream/{job_id}")
async def stream_optimization_status(job_id: str, token: str, db: AsyncSession = Depends(get_db)):
    async def event_generator():
        last_status = None
        while True:
            await db.commit() 
            stmt = select(ScadJob).where(ScadJob.id == job_id)
            result = await db.execute(stmt)
            job = result.scalar_one_or_none()

            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break

            if job.status != last_status:
                payload = {
                    "id": job.id,
                    "status": job.status,
                    "fitness_score": job.fitness_score,
                    "result_url": job.result_url,
                    "optimized_parameters": job.optimized_parameters,
                    "error": job.error
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_status = job.status
                print(f"Emitted update for job {job_id}: {payload}")

            if job.status.startswith("Completed") or job.status == "Failed":
                break   
            
            await asyncio.sleep(1.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")