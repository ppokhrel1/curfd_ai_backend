"""
Main FastAPI Application Entry Point

"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.exceptions import (
    MLServiceUnavailable,
    MLServiceTimeout,
    MLGenerationFailed,
    AppException,
)
from app.api.v1.api import api_router
from app.db.database import engine
from sqlmodel import SQLModel


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    # Startup: Create tables
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield

    # Shutdown: Cleanup
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend API for CURFD AI - 3D Model Generation and Simulation",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


#  CORS Configuration

if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            str(origin).rstrip("/") for origin in settings.BACKEND_CORS_ORIGINS
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Allow common local development origins if none specified
    # Note: allow_origins=["*"] is incompatible with allow_credentials=True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


#  Global Exception Handlers


@app.exception_handler(MLServiceUnavailable)
async def ml_service_unavailable_handler(request: Request, exc: MLServiceUnavailable):
    """Handle ML service unavailability (Req 6.2)."""
    return JSONResponse(
        status_code=503,
        content={"detail": exc.message, "retry_after": exc.retry_after},
        headers={"Retry-After": str(exc.retry_after)},
    )


@app.exception_handler(MLServiceTimeout)
async def ml_service_timeout_handler(request: Request, exc: MLServiceTimeout):
    """Handle ML service timeout."""
    return JSONResponse(
        status_code=504, content={"detail": exc.message, "retry_after": exc.retry_after}
    )


@app.exception_handler(MLGenerationFailed)
async def ml_generation_failed_handler(request: Request, exc: MLGenerationFailed):
    """Handle ML generation failures."""
    return JSONResponse(
        status_code=422,
        content={"detail": exc.message, "error_details": exc.error_details},
    )


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Handle application-level exceptions."""
    return JSONResponse(
        status_code=400, content={"detail": exc.message, "details": exc.details}
    )


# Include Routers

app.include_router(api_router, prefix=settings.API_V1_STR)

#  Root Endpoints


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint."""
    return {
        "message": "Welcome to CURFD AI Backend",
        "docs": "/docs",
        "version": "1.0.0",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "healthy", "service": settings.PROJECT_NAME, "version": "1.0.0"}


@app.get("/health/db", tags=["Health"])
async def database_health():
    """Database health check."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "database": str(e)}
        )
