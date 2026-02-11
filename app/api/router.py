from fastapi import APIRouter

from app.api.routes import (
    asset_meta,
    assets,
    auth,
    chat_stream,
    chats,
    health,
    jobs,
    messages,
    sessions,
    cadquery,
)

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(chats.router, prefix="/chats", tags=["chats"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
api_router.include_router(chat_stream.router, tags=["chat-stream"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(assets.router, prefix="/assets", tags=["assets"])
api_router.include_router(asset_meta.router, prefix="/asset-meta", tags=["asset-meta"])
api_router.include_router(cadquery.router, prefix="/cadquery", tags=["cadquery"])
