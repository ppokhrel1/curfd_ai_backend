from typing import List, Optional
import uuid
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.model import GenerationJob, GeneratedModel, Simulation

class ModelRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_job(self, job: GenerationJob) -> GenerationJob:
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def get_job(self, job_id: uuid.UUID) -> Optional[GenerationJob]:
        statement = select(GenerationJob).where(GenerationJob.id == job_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def update_job(self, job: GenerationJob) -> GenerationJob:
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def create_model(self, model: GeneratedModel) -> GeneratedModel:
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def get_model(self, model_id: uuid.UUID) -> Optional[GeneratedModel]:
        statement = select(GeneratedModel).where(GeneratedModel.id == model_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()
    
    async def get_user_models(self, user_id: uuid.UUID) -> List[GeneratedModel]:
        statement = select(GeneratedModel).where(GeneratedModel.user_id == user_id).order_by(GeneratedModel.created_at.desc())
        result = await self.session.execute(statement)
        return result.scalars().all()
