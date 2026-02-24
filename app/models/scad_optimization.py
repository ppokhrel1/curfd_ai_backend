from datetime import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel
from app.schemas.common import Timestamped 

class OptimizationParamSchema(BaseModel):
    name: str
    min_val: float
    max_val: float

class ScadJobCreate(BaseModel):
    chat_id: str 
    openscad_code: str
    parameters: List[OptimizationParamSchema]
    generations: int = 100
    population_size: int = 50

class ScadJobUpdate(BaseModel):
    status: Optional[str] = None
    worker_task_id: Optional[str] = None
    optimized_parameters: Optional[Dict[str, float]] = None
    fitness_score: Optional[float] = None
    result_url: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

class ScadJobRead(Timestamped):
    id: str
    chat_id: str
    status: str
    worker_task_id: Optional[str] = None
    openscad_code: str
    parameters: List[OptimizationParamSchema]
    generations: int
    population_size: int
    optimized_parameters: Optional[Dict[str, float]] = None
    fitness_score: Optional[float] = None
    result_url: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class WorkerWebhookPayload(BaseModel):
    status: str
    task_id: str
    optimized_parameters: Optional[Dict[str, float]] = None
    fitness_score: Optional[float] = None
    result_url: Optional[str] = None
    error: Optional[str] = None