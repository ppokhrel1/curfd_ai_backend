from fastapi import APIRouter

from app.api.routes import (
    assembly,
    asset_meta,
    assets,
    auth,
    chat_stream,
    chats,
    convert,
    experiments,
    health,
    init,
    jobs,
    messages,
    prompts,
    scad_genetic_algo,
    scad_versions,
    sessions,
    cadquery,
    gemini_openscad_generate_route,
    mesh_fill,
    openscad,
    storage_proxy,
    uploads,
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
api_router.include_router(gemini_openscad_generate_route.router, prefix="/openscad", tags=["openscad"])
api_router.include_router(openscad.router, prefix="/openscad-3d", tags=["OpenSCAD-3D"])
api_router.include_router(scad_genetic_algo.router, prefix="/scad-genetic-algo", tags=["scad-genetic-algo"])
api_router.include_router(scad_versions.router, prefix="/scad-versions", tags=["scad-versions"])
api_router.include_router(assembly.router, tags=["assembly"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
api_router.include_router(experiments.router, prefix="/experiments", tags=["experiments"])
api_router.include_router(init.router, prefix="/init", tags=["init"])
api_router.include_router(storage_proxy.router, prefix="/storage", tags=["storage-proxy"])
api_router.include_router(convert.router, prefix="/convert", tags=["convert"])
api_router.include_router(mesh_fill.router, prefix="/mesh", tags=["mesh-fill"])

