from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


class RunpodClient:
    def __init__(self, base_url: str, api_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}"}

    async def start_raw_job(
        self,
        input_payload: dict[str, Any],
        sync: bool = False,
    ) -> dict[str, Any]:
        """Submit an arbitrary input payload to the RunPod endpoint."""
        payload: dict[str, Any] = {"input": input_payload}
        endpoint = "runsync" if sync else "run"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/{endpoint}",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def start_job(
        self,
        action: str,
        prompt: str | None,
        requirements_json: dict | None,
        history: list[dict] | None = None,
        sync: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"input": {"action": action}}
        if prompt is not None:
            payload["input"]["prompt"] = prompt
        if requirements_json is not None:
            payload["input"]["requirements_json"] = requirements_json
        if history is not None:
            payload["input"]["history"] = history

        endpoint = "runsync" if sync else "run"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/{endpoint}",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_status(self, runpod_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/status/{runpod_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()


def get_runpod_client() -> RunpodClient:
    if not settings.runpod_api_token:
        raise ValueError("RUNPOD_API_TOKEN is not configured")
    return RunpodClient(settings.runpod_base_url, settings.runpod_api_token)


def get_image_to_3d_client() -> RunpodClient:
    base_url = settings.image_to_3d_runpod_base_url or settings.runpod_base_url
    token = settings.image_to_3d_runpod_api_token or settings.runpod_api_token
    if not token:
        raise ValueError("No API token configured for image-to-3D RunPod endpoint")
    return RunpodClient(base_url, token)
