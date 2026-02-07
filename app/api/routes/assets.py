from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.db.session import get_db
from app.models.asset import Asset as AssetModel
from app.models.job import Job as JobModel
from app.models.session import Session as SessionModel
from app.schemas.asset import AssetCreate, AssetRead

router = APIRouter()


@router.post("", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
def create_asset(
    payload: AssetCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    job = db.get(JobModel, payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.session and job.session.user_id and job.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
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
def list_assets(
    job_id: str | None = None,
    runpod_id: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    import logging
    from sqlalchemy import or_

    logger = logging.getLogger(__name__)
    logger.info(
        f"list_assets called: job_id={job_id}, runpod_id={runpod_id}, user_id={user_id}"
    )

    query = db.query(AssetModel).join(JobModel).join(SessionModel)
    query = query.filter(SessionModel.user_id == user_id)

    filters = []

    if job_id:
        filters.append(AssetModel.job_id == job_id)
        logger.info(f"Added job_id filter: {job_id}")

    if runpod_id:
        filters.append(AssetModel.metadata_json.op("->>")("runpod_id") == runpod_id)
        logger.info(f"Added runpod_id filter: {runpod_id}")

    if filters:
        if len(filters) == 1:
            query = query.filter(filters[0])
        else:
            query = query.filter(or_(*filters))

    results = query.order_by(AssetModel.created_at.desc()).all()
    logger.info(f"list_assets returning {len(results)} assets")

    return results


@router.get("/{asset_id}", response_model=AssetRead)
def get_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    asset = db.get(AssetModel, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.job and asset.job.session and asset.job.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(
    asset_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    asset = db.get(AssetModel, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.job and asset.job.session and asset.job.session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    db.delete(asset)
    db.commit()
    return None
