from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.deps import security
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)

router = APIRouter()


def _supabase_base_url() -> str:
    if not settings.supabase_url:
        raise HTTPException(
            status_code=500,
            detail="Supabase is not configured: missing SUPABASE_URL",
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


def _supabase_auth_request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> httpx.Response:
    api_key = _supabase_api_key()
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token or api_key}",
    }
    with httpx.Client(timeout=20.0) as client:
        return client.request(
            method,
            f"{_supabase_base_url()}{path}",
            headers=headers,
            json=json,
            params=params,
        )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterRequest):
    body: dict[str, Any] = {"email": payload.email, "password": payload.password}
    if payload.display_name:
        body["data"] = {"display_name": payload.display_name}

    response = _supabase_auth_request("POST", "/auth/v1/signup", json=body)
    if response.status_code >= 400:
        detail = _extract_error(response, "Failed to register user")
        raise HTTPException(
            status_code=409 if "registered" in detail.lower() else response.status_code,
            detail=detail,
        )

    payload_data = response.json()
    user = payload_data.get("user") or {}
    return RegisterResponse(
        message="Registration successful",
        user_id=user.get("id"),
        email=user.get("email"),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    if "@" not in payload.email:
        raise HTTPException(status_code=400, detail="Supabase login requires email")

    response = _supabase_auth_request(
        "POST",
        "/auth/v1/token",
        params={"grant_type": "password"},
        json={
            "email": payload.email,
            "password": payload.password,
        },
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=401 if response.status_code in (400, 401) else response.status_code,
            detail=_extract_error(response, "Invalid credentials"),
        )

    payload_data = response.json()
    user = payload_data.get("user") or {}
    access_token = payload_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="Supabase did not return access token")

    return TokenResponse(
        access_token=access_token,
        refresh_token=payload_data.get("refresh_token"),
        user_id=user.get("id", ""),
    )


@router.get("/me", response_model=dict)
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    response = _supabase_auth_request(
        "GET",
        "/auth/v1/user",
        access_token=credentials.credentials,
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=401, detail=_extract_error(response, "Invalid token"))
    user = response.json()
    return {
        "user_id": user.get("id"),
        "email": user.get("email"),
        "user_metadata": user.get("user_metadata", {}),
        "app_metadata": user.get("app_metadata", {}),
    }


@router.get("/logout", response_model=dict)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    response = _supabase_auth_request(
        "POST",
        "/auth/v1/logout",
        access_token=credentials.credentials,
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=401, detail=_extract_error(response, "Invalid token"))
    return {"status": "logged_out"}
