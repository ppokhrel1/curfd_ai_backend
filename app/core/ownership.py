"""Shared single-query ownership verification helpers.

Each helper fetches the entity AND verifies the user owns it via the
Session → ... join chain in a single query (instead of 2 separate queries).
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset as AssetModel
from app.models.asset_meta import AssetMeta as AssetMetaModel
from app.models.chat import Chat as ChatModel
from app.models.job import Job as JobModel
from app.models.message import Message as MessageModel
from app.models.session import Session as SessionModel


async def get_chat_verified(
    db: AsyncSession, chat_id: str, user_id: str
) -> ChatModel:
    """Fetch chat and verify ownership in one query."""
    row = (
        await db.execute(
            select(ChatModel, SessionModel.user_id)
            .join(SessionModel, ChatModel.session_id == SessionModel.id)
            .where(ChatModel.id == chat_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Chat not found")
    chat, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return chat


async def get_message_verified(
    db: AsyncSession, message_id: str, user_id: str
) -> MessageModel:
    """Fetch message and verify ownership in one query."""
    row = (
        await db.execute(
            select(MessageModel, SessionModel.user_id)
            .join(ChatModel, MessageModel.chat_id == ChatModel.id)
            .join(SessionModel, ChatModel.session_id == SessionModel.id)
            .where(MessageModel.id == message_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    message, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return message


async def get_job_verified(
    db: AsyncSession, job_id: str, user_id: str
) -> JobModel:
    """Fetch job and verify ownership in one query."""
    row = (
        await db.execute(
            select(JobModel, SessionModel.user_id)
            .join(SessionModel, JobModel.session_id == SessionModel.id)
            .where(JobModel.id == job_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    job, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return job


async def get_asset_verified(
    db: AsyncSession, asset_id: str, user_id: str
) -> AssetModel:
    """Fetch asset and verify ownership in one query."""
    row = (
        await db.execute(
            select(AssetModel, SessionModel.user_id)
            .join(JobModel, AssetModel.job_id == JobModel.id)
            .join(SessionModel, JobModel.session_id == SessionModel.id)
            .where(AssetModel.id == asset_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return asset


async def get_asset_meta_verified(
    db: AsyncSession, meta_id: str, user_id: str
) -> AssetMetaModel:
    """Fetch asset meta and verify ownership in one query."""
    row = (
        await db.execute(
            select(AssetMetaModel, SessionModel.user_id)
            .join(AssetModel, AssetMetaModel.asset_id == AssetModel.id)
            .join(JobModel, AssetModel.job_id == JobModel.id)
            .join(SessionModel, JobModel.session_id == SessionModel.id)
            .where(AssetMetaModel.id == meta_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset meta not found")
    meta, owner_id = row
    if owner_id and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return meta


async def verify_asset_owner(
    db: AsyncSession, asset_id: str, user_id: str
) -> str | None:
    """Return owner user_id for an asset, or raise 404/403."""
    row = (
        await db.execute(
            select(SessionModel.user_id)
            .join(JobModel, JobModel.session_id == SessionModel.id)
            .join(AssetModel, AssetModel.job_id == JobModel.id)
            .where(AssetModel.id == asset_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if row != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return row
