"""
Simulation Management API Routes

Implements:
- POST /simulation/start - Start simulation (Req 5.1)
- GET /simulation/{sim_id}/status - Get status (Req 5.2)
- GET /simulation/{sim_id}/results - Get results (Req 5.3)
- POST /simulation/{sim_id}/stop - Stop simulation (Req 5.4)
"""

from typing import Annotated, Optional, Dict, Any, List
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import get_current_user
from app.db.database import get_session
from app.models.user import User
from app.models.model import Simulation, GeneratedModel

router = APIRouter()


#  Request/Response Models


class StartSimulationRequest(BaseModel):
    model_id: uuid.UUID
    simulator: str = Field(default="gazebo", pattern="^(gazebo|isaac_sim)$")
    parameters: Optional[Dict[str, Any]] = None


class SimulationResponse(BaseModel):
    id: uuid.UUID
    model_id: uuid.UUID
    status: str
    simulator: str
    created_at: str
    completed_at: Optional[str] = None


class SimulationStatusResponse(BaseModel):
    id: uuid.UUID
    status: str
    progress: Optional[int] = None
    error: Optional[str] = None


class SimulationResultsResponse(BaseModel):
    id: uuid.UUID
    status: str
    results: Optional[Dict[str, Any]] = None
    completed_at: Optional[str] = None


#  Endpoints


@router.post(
    "/start", response_model=SimulationResponse, status_code=status.HTTP_202_ACCEPTED
)
async def start_simulation(
    request: StartSimulationRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Start a physics simulation with a generated model.

    Returns simulation job ID immediately.
    Supports 'gazebo' and 'isaac_sim' simulators.
    """
    # Verify model ownership
    stmt = select(GeneratedModel).where(GeneratedModel.id == request.model_id)
    result = await session.execute(stmt)
    model = result.scalar_one_or_none()

    if not model or model.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    # Create simulation job
    simulation = Simulation(
        model_id=request.model_id,
        user_id=current_user.id,
        simulator=request.simulator,
        status="queued",
        parameters=request.parameters or {},
    )
    session.add(simulation)
    await session.commit()
    await session.refresh(simulation)

    # In production, this would trigger actual simulation
    # background_tasks.add_task(run_simulation, simulation.id)

    return SimulationResponse(
        id=simulation.id,
        model_id=simulation.model_id,
        status=simulation.status,
        simulator=simulation.simulator,
        created_at=simulation.created_at.isoformat(),
    )


@router.get("/{sim_id}/status", response_model=SimulationStatusResponse)
async def get_simulation_status(
    sim_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get current status of a simulation job."""
    stmt = select(Simulation).where(Simulation.id == sim_id)
    result = await session.execute(stmt)
    simulation = result.scalar_one_or_none()

    if not simulation or simulation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found"
        )

    # Calculate progress
    progress = None
    if simulation.status == "queued":
        progress = 0
    elif simulation.status == "running":
        progress = 50
    elif simulation.status == "completed":
        progress = 100

    return SimulationStatusResponse(
        id=simulation.id, status=simulation.status, progress=progress
    )


@router.get("/{sim_id}/results", response_model=SimulationResultsResponse)
async def get_simulation_results(
    sim_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get results of a completed simulation."""
    stmt = select(Simulation).where(Simulation.id == sim_id)
    result = await session.execute(stmt)
    simulation = result.scalar_one_or_none()

    if not simulation or simulation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found"
        )

    return SimulationResultsResponse(
        id=simulation.id,
        status=simulation.status,
        results=simulation.results,
        completed_at=(
            simulation.completed_at.isoformat() if simulation.completed_at else None
        ),
    )


@router.post("/{sim_id}/stop", status_code=status.HTTP_200_OK)
async def stop_simulation(
    sim_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Stop a running simulation."""
    stmt = select(Simulation).where(Simulation.id == sim_id)
    result = await session.execute(stmt)
    simulation = result.scalar_one_or_none()

    if not simulation or simulation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found"
        )

    if simulation.status not in ["queued", "running"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Simulation is not running"
        )

    simulation.status = "stopped"
    simulation.completed_at = datetime.utcnow()
    session.add(simulation)
    await session.commit()

    return {"message": "Simulation stopped", "id": str(simulation.id)}


@router.get("", response_model=List[SimulationResponse])
async def list_simulations(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """List all simulations for the authenticated user."""
    stmt = (
        select(Simulation)
        .where(Simulation.user_id == current_user.id)
        .order_by(Simulation.created_at.desc())
    )
    result = await session.execute(stmt)
    simulations = result.scalars().all()

    return [
        SimulationResponse(
            id=s.id,
            model_id=s.model_id,
            status=s.status,
            simulator=s.simulator,
            created_at=s.created_at.isoformat(),
            completed_at=s.completed_at.isoformat() if s.completed_at else None,
        )
        for s in simulations
    ]
