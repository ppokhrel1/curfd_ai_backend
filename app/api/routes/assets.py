from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.schemas.asset import AssetCreate, AssetRead

router = APIRouter()


@router.post("", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    asset = AssetModel(
        job_id=payload.job_id,
        asset_type=payload.asset_type,
        uri=payload.uri,
        storage_provider=payload.storage_provider,
        metadata_json=payload.metadata_json,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.get("", response_model=list[AssetRead])
def list_assets(job_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(AssetModel)
    if job_id:
        query = query.filter(AssetModel.job_id == job_id)
    return query.order_by(AssetModel.created_at.desc()).all()


@router.get("/{asset_id}", response_model=AssetRead)
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(AssetModel, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(AssetModel, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(asset)
    db.commit()
    return None
