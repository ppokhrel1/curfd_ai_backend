from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user_id
from app.core.ownership import get_asset_meta_verified, verify_asset_owner
from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.job import Job as JobModel
from app.models.session import Session as SessionModel
from app.schemas.asset_meta import AssetMetaCreate, AssetMetaRead, AssetMetaUpdate

router = APIRouter()


async def _validate_composite_ids(
    db: AsyncSession, composite_ids: list[str]
) -> list[AssetModel]:
    """Validate all composite IDs exist in a single query (no N+1)."""
    if not composite_ids:
        return []
    result = await db.execute(
        select(AssetModel).where(AssetModel.id.in_(composite_ids))
    )
    found = result.scalars().all()
    found_ids = {a.id for a in found}
    missing = set(composite_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"composite asset not found: {missing.pop()}",
        )
    return list(found)


@router.post("", response_model=AssetMetaRead, status_code=status.HTTP_201_CREATED)
async def create_asset_meta(
    payload: AssetMetaCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    # Single query: verify asset exists + ownership
    owner = await verify_asset_owner(db, payload.asset_id, user_id)

    if payload.component_of:
        component_asset = await db.get(AssetModel, payload.component_of)
        if not component_asset:
            raise HTTPException(status_code=404, detail="component_of asset not found")

    composite_ids = payload.is_composite_of or []
    composite_assets = await _validate_composite_ids(db, composite_ids)

    data = payload.model_dump(exclude={"is_composite_of"})
    meta = AssetMetaModel(**data)
    meta.uploaded_by = owner
    if composite_assets:
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
    return await get_asset_meta_verified(db, meta_id, user_id)


@router.patch("/{meta_id}", response_model=AssetMetaRead)
async def update_asset_meta(
    meta_id: str,
    payload: AssetMetaUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = await get_asset_meta_verified(db, meta_id, user_id)

    updates = payload.model_dump(exclude_unset=True)
    composite_ids = updates.pop("is_composite_of", None)
    if "component_of" in updates and updates["component_of"]:
        component_asset = await db.get(AssetModel, updates["component_of"])
        if not component_asset:
            raise HTTPException(status_code=404, detail="component_of asset not found")

    # Validate all composite IDs in a single query (no N+1)
    if composite_ids:
        composite_assets = await _validate_composite_ids(db, composite_ids)
    elif composite_ids is not None:
        composite_assets = []
    else:
        composite_assets = None

    for key, value in updates.items():
        setattr(meta, key, value)

    if composite_assets is not None:
        meta.composite_assets = composite_assets
    await db.commit()
    await db.refresh(meta)
    return meta


@router.delete("/{meta_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset_meta(
    meta_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = await get_asset_meta_verified(db, meta_id, user_id)
    await db.delete(meta)
    await db.commit()
    return None
