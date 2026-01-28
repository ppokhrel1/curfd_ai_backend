"""
API Router Aggregation

"""

from fastapi import APIRouter

from app.api.v1.routers import auth, chat, models, simulation, proxy

api_router = APIRouter()


@api_router.get("/", tags=["Root"])
async def api_root():
    return {"message": "Welcome to CURFD AI API v1"}


api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat Sessions"])
api_router.include_router(models.router, prefix="/models", tags=["Model Generation"])
api_router.include_router(simulation.router, prefix="/simulation", tags=["Simulation"])
api_router.include_router(proxy.router, prefix="/proxy", tags=["Proxy"])
