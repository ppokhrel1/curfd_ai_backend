from typing import Any

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import Depends, HTTPException, status
from sqlalchemy import select

from app.core.jwt import decode_access_token, token_hash
from app.db.session import get_db, get_db_async
from app.models.revoked_token import RevokedToken
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Any

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

security = HTTPBearer()


def _supabase_base_url() -> str:
    if not settings.supabase_url:
        raise HTTPException(
            status_code=500, detail="Supabase is not configured: missing SUPABASE_URL"
        )
    base_url = settings.supabase_url.strip()
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
    return base_url.rstrip("/")


def _supabase_api_key() -> str:
    api_key = settings.supabase_anon_key or settings.supabase_service_role_key
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase is not configured: missing SUPABASE_ANON_KEY",
        )
    return api_key


def _extract_error(response: httpx.Response, fallback: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        return fallback
    if isinstance(payload, dict):
        return (
            payload.get("msg")
            or payload.get("error_description")
            or payload.get("error")
            or payload.get("message")
            or fallback
        )
    return fallback


def get_supabase_user(token: str) -> dict[str, Any]:
    headers = {
        "apikey": _supabase_api_key(),
        "Authorization": f"Bearer {token}",
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(f"{_supabase_base_url()}/auth/v1/user", headers=headers)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Supabase auth service unavailable")

    if response.status_code >= 400:
        raise HTTPException(status_code=401, detail=_extract_error(response, "Invalid token"))

    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("id"):
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials
    user = get_supabase_user(token)
    return str(user["id"])

async def get_current_user_id_async(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_async),
) -> str:
    token = credentials.credentials
    user_id = decode_access_token(token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token"
        )

    # Hash the token to check revocation
    token_digest = token_hash(token)
    
    # ASYNC DATABASE CHECK
    stmt = select(RevokedToken).where(RevokedToken.token_hash == token_digest)
    result = await db.execute(stmt)
    revoked = result.scalar_one_or_none()
    
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token revoked"
        )

    return user_id
