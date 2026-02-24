from fastapi import APIRouter

from app.api.routes import (
    asset_meta,
    assets,
    auth,
    chat_stream,
    chats,
    gemini_routes,
    health,
    jobs,
    messages,
    scad_genetic_algo,
    sessions,
    cadquery,
    gemini_openscad_generate_route,
    openscad
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
api_router.include_router(gemini_routes.router, prefix="/gemini", tags=["gemini"])
api_router.include_router(cadquery.router, prefix="/cadquery", tags=["cadquery"])
api_router.include_router(gemini_openscad_generate_route.router, prefix="/openscad", tags=["openscad"])
api_router.include_router(openscad.router, prefix="/openscad-3d", tags=["OpenSCAD-3D"])
api_router.include_router(scad_genetic_algo.router, prefix="/scad-genetic-algo", tags=["scad-genetic-algo"])

