from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.job import Job as JobModel
from app.models.session import Session as SessionModel
from app.schemas.asset_meta import AssetMetaCreate, AssetMetaRead, AssetMetaUpdate

router = APIRouter()


@router.post("", response_model=AssetMetaRead, status_code=status.HTTP_201_CREATED)
def create_asset_meta(
    payload: AssetMetaCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    asset = db.get(AssetModel, payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.job and asset.job.session and asset.job.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if payload.component_of:
        component_asset = db.get(AssetModel, payload.component_of)
        if not component_asset:
            raise HTTPException(status_code=404, detail="component_of asset not found")

    composite_ids = payload.is_composite_of or []
    if composite_ids:
        for asset_id in composite_ids:
            if not db.get(AssetModel, asset_id):
                raise HTTPException(status_code=404, detail=f"composite asset not found: {asset_id}")

    data = payload.model_dump(exclude={"is_composite_of"})
    meta = AssetMetaModel(**data)
    meta.uploaded_by = asset.job.session.user_id if asset.job and asset.job.session else None
    if composite_ids:
        meta.composite_assets = [db.get(AssetModel, asset_id) for asset_id in composite_ids]
    db.add(meta)
    db.commit()
    db.refresh(meta)
    return meta


@router.get("", response_model=list[AssetMetaRead])
def list_asset_meta(
    asset_id: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    query = db.query(AssetMetaModel).join(AssetModel).join(JobModel).join(SessionModel)
    query = query.filter(SessionModel.user_id == user_id)
    if asset_id:
        query = query.filter(AssetMetaModel.asset_id == asset_id)
    return query.order_by(AssetMetaModel.created_at.desc()).all()


@router.get("/{meta_id}", response_model=AssetMetaRead)
def get_asset_meta(
    meta_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = db.get(AssetMetaModel, meta_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    if (
        meta.asset
        and meta.asset.job
        and meta.asset.job.session
        and meta.asset.job.session.user_id != user_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return meta


@router.patch("/{meta_id}", response_model=AssetMetaRead)
def update_asset_meta(
    meta_id: str,
    payload: AssetMetaUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = db.get(AssetMetaModel, meta_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    if (
        meta.asset
        and meta.asset.job
        and meta.asset.job.session
        and meta.asset.job.session.user_id != user_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    updates = payload.model_dump(exclude_unset=True)
    composite_ids = updates.pop("is_composite_of", None)
    if "component_of" in updates and updates["component_of"]:
        component_asset = db.get(AssetModel, updates["component_of"])
        if not component_asset:
            raise HTTPException(status_code=404, detail="component_of asset not found")
    if composite_ids:
        for asset_id in composite_ids:
            if not db.get(AssetModel, asset_id):
                raise HTTPException(status_code=404, detail=f"composite asset not found: {asset_id}")

    for key, value in updates.items():
        setattr(meta, key, value)

    if composite_ids is not None:
        meta.composite_assets = [db.get(AssetModel, asset_id) for asset_id in composite_ids]
    meta.uploaded_by = (
        meta.asset.job.session.user_id
        if meta.asset and meta.asset.job and meta.asset.job.session
        else meta.uploaded_by
    )
    db.commit()
    db.refresh(meta)
    return meta


@router.delete("/{meta_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset_meta(
    meta_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    meta = db.get(AssetMetaModel, meta_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    if (
        meta.asset
        and meta.asset.job
        and meta.asset.job.session
        and meta.asset.job.session.user_id != user_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    db.delete(meta)
    db.commit()
    return None
