"""
Pydantic schemas for API request/response models.
Separates API contracts from database models.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


#  Auth Schemas


class TokenBase(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserBase(BaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserResponse(UserBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


class TokenWithUser(TokenBase):
    user: UserResponse


#  Chat Schemas


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class SessionResponse(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    last_message_at: Optional[datetime] = None
    message_count: int = 0

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    trigger_generation: bool = False


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime
    model_generation_id: Optional[UUID] = None

    class Config:
        from_attributes = True


#  Model Generation Schemas


class GenerateModelRequest(BaseModel):
    session_id: UUID
    prompt: Optional[str] = None


class GenerationJobResponse(BaseModel):
    job_id: UUID
    status: str
    created_at: datetime
    message: Optional[str] = None

    class Config:
        from_attributes = True


class MeshFileResponse(BaseModel):
    filename: str
    url: str
    format: str


class ModelFilesResponse(BaseModel):
    sdf_url: Optional[str] = None
    urdf_url: Optional[str] = None
    config_url: Optional[str] = None
    meshes: List[MeshFileResponse] = []


class ModelResponse(BaseModel):
    id: UUID
    name: str
    status: str
    specification: Dict[str, Any] = {}
    files: ModelFilesResponse
    created_at: datetime
    generation_time: Optional[float] = None

    class Config:
        from_attributes = True


class ModelStatusResponse(BaseModel):
    status: str
    progress: Optional[int] = None
    error: Optional[str] = None


#  Simulation Schemas


class StartSimulationRequest(BaseModel):
    model_id: UUID
    simulator: str = Field(default="gazebo", pattern="^(gazebo|isaac_sim)$")
    parameters: Optional[Dict[str, Any]] = None


class SimulationResponse(BaseModel):
    id: UUID
    model_id: UUID
    status: str
    simulator: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    results: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


#  Error Schemas


class ErrorResponse(BaseModel):
    detail: str
    errors: Optional[List[Dict[str, Any]]] = None


class ServiceUnavailableResponse(BaseModel):
    detail: str
    retry_after: int = 30
