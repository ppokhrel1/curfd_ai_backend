import uuid
from datetime import datetime
from typing import Optional, Dict, List
from sqlmodel import Field, SQLModel, Column, JSON

class GenerationJob(SQLModel, table=True):
    __tablename__ = "generation_jobs"
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID
    session_id: uuid.UUID
    status: str # "queued", "processing", "completed", "failed"
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

class GeneratedModel(SQLModel, table=True):
    __tablename__ = "generated_models"
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="generation_jobs.id")
    user_id: uuid.UUID
    name: str
    specification: Dict = Field(sa_column=Column(JSON))
    files: Dict = Field(sa_column=Column(JSON))
    generation_time: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Simulation(SQLModel, table=True):
    __tablename__ = "simulations"
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    model_id: uuid.UUID = Field(foreign_key="generated_models.id")
    user_id: uuid.UUID
    simulator: str # "gazebo" or "isaac_sim"
    status: str
    parameters: Optional[Dict] = Field(default=None, sa_column=Column(JSON))
    results: Optional[Dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
