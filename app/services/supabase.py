from __future__ import annotations

from typing import Any, Literal

import httpx

from app.core.config import settings


class SupabaseClient:
    """Minimal Supabase REST client wrapper (PostgREST + Storage/Auth stubs)."""

    def __init__(self, api_url: str, api_key: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"],
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.api_url}/{path.lstrip('/')}"
        with httpx.Client(timeout=20.0) as client:
            return client.request(method, url, headers=self._headers(), json=json, params=params)


supabase_anon = None
supabase_service = None

if settings.supabase_url and settings.supabase_anon_key:
    supabase_anon = SupabaseClient(settings.supabase_url, settings.supabase_anon_key)

if settings.supabase_url and settings.supabase_service_role_key:
    supabase_service = SupabaseClient(
        settings.supabase_url, settings.supabase_service_role_key
    )
