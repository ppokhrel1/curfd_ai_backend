from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.job import Job as JobModel
from app.models.session import Session as SessionModel
from app.schemas.asset_meta import AssetMetaCreate, AssetMetaRead, AssetMetaUpdate

router = APIRouter()


@router.post("", response_model=AssetMetaRead, status_code=status.HTTP_201_CREATED)
async def create_asset_meta(
    payload: AssetMetaCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    asset = await db.get(AssetModel, payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .join(AssetModel, AssetModel.job_id == JobModel.id)
        .where(AssetModel.id == payload.asset_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if payload.component_of:
        component_asset = await db.get(AssetModel, payload.component_of)
        if not component_asset:
            raise HTTPException(status_code=404, detail="component_of asset not found")

    composite_ids = payload.is_composite_of or []
    if composite_ids:
        for asset_id in composite_ids:
            if not await db.get(AssetModel, asset_id):
                raise HTTPException(status_code=404, detail=f"composite asset not found: {asset_id}")

    data = payload.model_dump(exclude={"is_composite_of"})
    meta = AssetMetaModel(**data)
    meta.uploaded_by = owner
    if composite_ids:
        composite_assets = []
        for asset_id in composite_ids:
            composite_asset = await db.get(AssetModel, asset_id)
            if composite_asset:
                composite_assets.append(composite_asset)
        meta.composite_assets = composite_assets
    db.add(meta)
    await db.commit()
    await db.refresh(meta)
    return meta


@router.get("", response_model=list[AssetMetaRead])
async def list_asset_meta(
    asset_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    stmt = (
        select(AssetMetaModel)
        .join(AssetModel, AssetMetaModel.asset_id == AssetModel.id)
        .join(JobModel, AssetModel.job_id == JobModel.id)
        .join(SessionModel, JobModel.session_id == SessionModel.id)
        .where(SessionModel.user_id == user_id)
    )
    if asset_id:
        stmt = stmt.where(AssetMetaModel.asset_id == asset_id)
    result = await db.execute(stmt.order_by(AssetMetaModel.created_at.desc()))
    return result.scalars().all()


@router.get("/{meta_id}", response_model=AssetMetaRead)
async def get_asset_meta(
    meta_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = await db.get(AssetMetaModel, meta_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .join(AssetModel, AssetModel.job_id == JobModel.id)
        .join(AssetMetaModel, AssetMetaModel.asset_id == AssetModel.id)
        .where(AssetMetaModel.id == meta_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return meta


@router.patch("/{meta_id}", response_model=AssetMetaRead)
async def update_asset_meta(
    meta_id: str,
    payload: AssetMetaUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = await db.get(AssetMetaModel, meta_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .join(AssetModel, AssetModel.job_id == JobModel.id)
        .join(AssetMetaModel, AssetMetaModel.asset_id == AssetModel.id)
        .where(AssetMetaModel.id == meta_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    updates = payload.model_dump(exclude_unset=True)
    composite_ids = updates.pop("is_composite_of", None)
    if "component_of" in updates and updates["component_of"]:
        component_asset = await db.get(AssetModel, updates["component_of"])
        if not component_asset:
            raise HTTPException(status_code=404, detail="component_of asset not found")
    if composite_ids:
        for asset_id in composite_ids:
            if not await db.get(AssetModel, asset_id):
                raise HTTPException(status_code=404, detail=f"composite asset not found: {asset_id}")

    for key, value in updates.items():
        setattr(meta, key, value)

    if composite_ids is not None:
        composite_assets = []
        for asset_id in composite_ids:
            composite_asset = await db.get(AssetModel, asset_id)
            if composite_asset:
                composite_assets.append(composite_asset)
        meta.composite_assets = composite_assets
    if owner:
        meta.uploaded_by = owner
    await db.commit()
    await db.refresh(meta)
    return meta


@router.delete("/{meta_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset_meta(
    meta_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = await db.get(AssetMetaModel, meta_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    owner = await db.scalar(
        select(SessionModel.user_id)
        .join(JobModel, JobModel.session_id == SessionModel.id)
        .join(AssetModel, AssetModel.job_id == JobModel.id)
        .join(AssetMetaModel, AssetMetaModel.asset_id == AssetModel.id)
        .where(AssetMetaModel.id == meta_id)
    )
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    await db.delete(meta)
    await db.commit()
    return None
