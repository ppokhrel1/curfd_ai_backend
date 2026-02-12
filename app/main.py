from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.jwt import create_access_token_with_exp, decode_token_payload, token_hash
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.revoked_token import RevokedToken
import app.models  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy import select
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware

def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.project_name, redirect_slashes=False)

    allowed_hosts = [host.strip() for host in settings.trusted_hosts.split(",") if host.strip()]
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts,
    )
    # Trust proxy headers (X-Forwarded-Proto, etc.) to ensure redirects use HTTPS
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

    # robustly parse origins
    raw_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
    origins = []
    for origin in raw_origins:
        origins.append(origin)
        if not origin.startswith("http"):
            origins.append(f"https://{origin}")
            origins.append(f"http://{origin}")
            
    print(f"DEBUG: Loaded CORS Origins: {origins}")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def log_request_origin(request, call_next):
        origin = request.headers.get("origin")
        if origin:
            print(f"DEBUG: Incoming request from Origin: {origin}")
        return await call_next(request)

    @app.middleware("http")
    async def refresh_access_token(request, call_next):
        auth_header = request.headers.get("Authorization", "")
        refresh_token = None
        refresh_exp = None

        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            payload = decode_token_payload(token, verify_exp=True)
            if payload and payload.get("sub"):
                exp = payload.get("exp")
                if isinstance(exp, (int, float)):
                    now_ts = datetime.now(timezone.utc).timestamp()
                    seconds_left = exp - now_ts
                    if 0 < seconds_left <= 600:
                        async with SessionLocal() as db:
                            try:
                                stmt = select(RevokedToken).where(
                                    RevokedToken.token_hash == token_hash(token)
                                )
                                result = await db.execute(stmt)
                                revoked = result.scalars().first()
                            finally:
                                await db.close()
                        if not revoked:
                            new_exp = datetime.fromtimestamp(exp, tz=timezone.utc) + timedelta(
                                minutes=10
                            )
                            refresh_exp = new_exp
                            refresh_token = create_access_token_with_exp(
                                subject=payload["sub"],
                                expires_at=new_exp,
                            )

        response = await call_next(request)
        if refresh_token and refresh_exp:
            response.headers["X-Refresh-Token"] = refresh_token
            response.headers["X-Refresh-Token-Expires-At"] = refresh_exp.isoformat()
        return response

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.on_event("startup")
    async def on_startup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    return app


app = create_app()