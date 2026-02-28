import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, or_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.job import Job as JobModel
from app.models.session import Session as SessionModel
from app.schemas.asset import AssetCreate, AssetRead, AssetSearchResult

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/search", response_model=list[AssetSearchResult])
async def search_assets(
    q: str = Query("", min_length=0, description="Search query for part names"),
    category: str | None = Query(None, description="Filter by model_type category"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Search individual parts by name with optional category filter.
    Only returns openscad_part assets (not full models) so swap works at part level.
    """
    stmt = (
        select(
            AssetModel,
            AssetMetaModel.part_name,
            AssetMetaModel.component_of,
            AssetMetaModel.position_json,
            AssetMetaModel.material_json,
        )
        .join(AssetMetaModel, AssetMetaModel.asset_id == AssetModel.id)
        .join(JobModel, AssetModel.job_id == JobModel.id)
        .join(SessionModel, JobModel.session_id == SessionModel.id)
        .where(SessionModel.user_id == user_id)
        .where(AssetModel.asset_type == "openscad_part")
    )

    if q:
        stmt = stmt.where(AssetMetaModel.part_name.ilike(f"%{q}%"))

    if category:
        stmt = stmt.where(
            AssetModel.metadata_json.op("->>")("model_type") == category
        )

    # Order: exact match first, then partial, then by date
    if q:
        stmt = stmt.order_by(
            case(
                (func.lower(AssetMetaModel.part_name) == q.lower(), 0),
                else_=1,
            ),
            AssetModel.created_at.desc(),
        )
    else:
        stmt = stmt.order_by(AssetModel.created_at.desc())

    stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        AssetSearchResult(
            id=asset.id,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
            job_id=asset.job_id,
            asset_type=asset.asset_type,
            uri=asset.uri,
            storage_provider=asset.storage_provider,
            metadata_json=asset.metadata_json,
            part_name=part_name,
            component_of=component_of,
            position_json=position_json,
            material_json=material_json,
        )
        for asset, part_name, component_of, position_json, material_json in rows
    ]


@router.post("", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    job = await db.get(JobModel, payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .where(JobModel.id == payload.job_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    asset = AssetModel(
        job_id=payload.job_id,
        asset_type=payload.asset_type,
        uri=payload.uri,
        storage_provider=payload.storage_provider,
        metadata_json=payload.metadata_json,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("", response_model=list[AssetRead])
async def list_assets(
    job_id: str | None = None,
    runpod_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    logger.info(
        f"list_assets called: job_id={job_id}, runpod_id={runpod_id}, user_id={user_id}"
    )

    stmt = (
        select(AssetModel)
        .join(JobModel, AssetModel.job_id == JobModel.id)
        .join(SessionModel, JobModel.session_id == SessionModel.id)
        .where(SessionModel.user_id == user_id)
    )

    filters = []

    if job_id:
        filters.append(AssetModel.job_id == job_id)
        logger.info(f"Added job_id filter: {job_id}")

    if runpod_id:
        filters.append(AssetModel.metadata_json.op("->>")("runpod_id") == runpod_id)
        logger.info(f"Added runpod_id filter: {runpod_id}")

    if filters:
        if len(filters) == 1:
            stmt = stmt.where(filters[0])
        else:
            stmt = stmt.where(or_(*filters))

    result = await db.execute(stmt.order_by(AssetModel.created_at.desc()))
    results = result.scalars().all()
    logger.info(f"list_assets returning {len(results)} assets")

    return results


@router.get("/{asset_id}", response_model=AssetRead)
async def get_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    asset = await db.get(AssetModel, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .join(AssetModel, AssetModel.job_id == JobModel.id)
        .where(AssetModel.id == asset_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    asset = await db.get(AssetModel, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .join(AssetModel, AssetModel.job_id == JobModel.id)
        .where(AssetModel.id == asset_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.delete(asset)
    await db.commit()
    return None
