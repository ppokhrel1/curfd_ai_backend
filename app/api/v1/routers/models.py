"""
Model Generation and Viewer API Routes

Implements:
- POST /models/generate - Trigger generation (Req 3.1)
- GET /models/{job_id}/status - Job status (Req 3.5)
- GET /models - List user models (Req 3.4)
- GET /models/{model_id} - Model details (Req 3.3)
- GET /models/{model_id}/files - File URLs for viewer (Req 4.1)
- GET /models/{model_id}/metadata - Metadata for viewer (Req 4.2)
"""

from typing import List, Annotated, Optional, Dict, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Response
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession
import httpx

from app.core.security import get_current_user
from app.core.exceptions import (
    MLServiceUnavailable,
    MLServiceTimeout,
    MLGenerationFailed,
)
from app.db.database import get_session
from app.models.user import User
from app.repositories.model_repo import ModelRepository
from app.repositories.chat_repo import ChatRepository
from app.services.model_service import ModelService
from app.services.ml_client import ml_client

router = APIRouter()


#  Request/Response Models


class GenerateModelRequest(BaseModel):
    session_id: uuid.UUID
    prompt: Optional[str] = None


class GenerationJobResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    created_at: str
    message: Optional[str] = None


class ModelStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: Optional[int] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class MeshFile(BaseModel):
    filename: str
    url: str
    format: str


class ModelFilesResponse(BaseModel):
    sdf_url: Optional[str] = None
    urdf_url: Optional[str] = None
    config_url: Optional[str] = None
    meshes: List[MeshFile] = []


class ModelMetadataResponse(BaseModel):
    id: str
    name: str
    assembly_plan: Dict[str, Any] = {}
    parts_list: List[Dict[str, Any]] = []
    joint_configurations: List[Dict[str, Any]] = []
    generation_time: Optional[float] = None
    created_at: str


class ModelResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    files: ModelFilesResponse
    created_at: str
    generation_time: Optional[float] = None


class ServiceUnavailableResponse(BaseModel):
    detail: str
    retry_after: int


#  Helper Functions


async def _run_generation(model_service: ModelService, job_id: uuid.UUID, prompt: str):
    """Background task to run model generation."""
    try:
        await model_service.trigger_generation(job_id, prompt)
    except Exception as e:
        # Error already recorded in job status
        pass


#  Endpoints


@router.post(
    "/generate",
    response_model=GenerationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={503: {"model": ServiceUnavailableResponse}},
)
async def generate_model(
    request: GenerateModelRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Trigger 3D model generation from chat session context.

    Returns job ID immediately. Use /models/{job_id}/status to poll for completion.
    Returns 503 if ML service is unavailable.
    """
    model_repo = ModelRepository(session)
    chat_repo = ChatRepository(session)
    model_service = ModelService(model_repo, ml_client, chat_repo)

    # Verify session ownership
    chat_session = await chat_repo.get_session(request.session_id)
    if not chat_session or chat_session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )

    # Prepare prompt from session context if not provided
    prompt = request.prompt
    if not prompt:
        messages = await chat_repo.get_messages(request.session_id)
        if messages:
            prompt = " ".join([m.content for m in messages[-5:]])  # Last 5 messages
        else:
            prompt = "Generate a robotic arm model"

    # Create job
    job = await model_service.create_generation_job(
        current_user.id, request.session_id, prompt
    )

    # Trigger generation in background
    background_tasks.add_task(_run_generation, model_service, job.id, prompt)

    return GenerationJobResponse(
        job_id=job.id,
        status="queued",
        created_at=job.created_at.isoformat(),
        message="Model generation queued successfully",
    )


@router.get("/{job_id}/status", response_model=ModelStatusResponse)
async def get_generation_status(
    job_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Get status of a model generation job.

    Poll this endpoint to track generation progress.
    """
    model_repo = ModelRepository(session)
    model_service = ModelService(model_repo, ml_client)

    job = await model_repo.get_job(job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    status_data = await model_service.get_job_status(job_id)
    return ModelStatusResponse(**status_data)


@router.get("", response_model=List[ModelResponse])
async def list_models(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """List all generated models for the authenticated user."""
    model_repo = ModelRepository(session)
    model_service = ModelService(model_repo, ml_client)

    models = await model_service.get_user_models(current_user.id)

    return [
        ModelResponse(
            id=m.id,
            name=m.name,
            status="completed",
            files=ModelFilesResponse(**(m.files or {})),
            created_at=m.created_at.isoformat(),
            generation_time=m.generation_time,
        )
        for m in models
    ]


@router.get("/{model_id}", response_model=ModelResponse)
async def get_model(
    model_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get details of a specific generated model."""
    model_repo = ModelRepository(session)
    model_service = ModelService(model_repo, ml_client)

    model = await model_service.get_model(model_id)
    if not model or model.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    return ModelResponse(
        id=model.id,
        name=model.name,
        status="completed",
        files=ModelFilesResponse(**(model.files or {})),
        created_at=model.created_at.isoformat(),
        generation_time=model.generation_time,
    )


@router.get("/{model_id}/files", response_model=ModelFilesResponse)
async def get_model_files(
    model_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Get file URLs for loading model in 3D viewer.

    Returns signed URLs or direct URLs to SDF/URDF and mesh files.
    """
    model_repo = ModelRepository(session)
    model_service = ModelService(model_repo, ml_client)

    model = await model_repo.get_model(model_id)
    if not model or model.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    files = await model_service.get_model_files(model_id)
    return ModelFilesResponse(**files)


@router.get("/{model_id}/metadata", response_model=ModelMetadataResponse)
async def get_model_metadata(
    model_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Get model metadata for viewer configuration.

    Returns assembly plan, parts list, and joint configurations.
    """
    model_repo = ModelRepository(session)
    model_service = ModelService(model_repo, ml_client)

    model = await model_repo.get_model(model_id)
    if not model or model.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    metadata = await model_service.get_model_metadata(model_id)
    return ModelMetadataResponse(**metadata)
