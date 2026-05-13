"""Modal client that mirrors RunpodClient's interface for image_to_3d.

The Modal HTTP endpoint at /image_to_3d is synchronous (the request
blocks for the full 3-5 min generation), but the backend's existing
flow assumes a RunPod-style async job: `start_raw_job` returns a job
id, then `_runpod_poll_and_emit()` polls until the job completes.

To reuse all of that polling + WebSocket-emit code without refactoring
chat_stream.py, this client fakes the async-job interface on top of
Modal's sync HTTP:

  - `start_raw_job(payload)` immediately returns a synthetic id and
    kicks off an asyncio task that POSTs to Modal and stashes the
    result in a process-local dict.
  - `get_status(id)` looks up the id in that dict:
        IN_PROGRESS  → task still running
        COMPLETED    → wraps the Modal response in RunPod's status shape
        FAILED       → reports the exception

The output dict mirrors RunPod's response exactly so
`_persist_image_to_3d_asset()` and the WebSocket payloads downstream
keep working without changes.

When the backend restarts, in-flight jobs are lost (same as RunPod
would on a worker restart in the middle of a job).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ─── Per-process call registry ────────────────────────────────────────────
# Maps synthetic job_id → asyncio.Task that runs the HTTP POST.
# Tasks store their result on completion (`task.result()` returns the
# Modal response dict) or their exception (`task.exception()`).
_CALLS: dict[str, asyncio.Task] = {}


# ─── Status shape (matches what RunpodClient.get_status returns) ─────────
def _status_in_progress(job_id: str) -> dict[str, Any]:
    return {"id": job_id, "status": "IN_PROGRESS"}


def _status_completed(job_id: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job_id,
        "status": "COMPLETED",
        "output": output,
        # RunPod returns these too; downstream sometimes logs them. We
        # leave them at 0 because Modal doesn't surface comparable
        # timings via HTTP (they're visible in the Modal dashboard).
        "executionTime": 0,
        "delayTime": 0,
    }


def _status_failed(job_id: str, error: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "status": "FAILED",
        "error": error,
    }


class ModalClient:
    """HTTP-backed Modal client with a RunPod-compatible interface.

    Only `image_to_3d` is implemented — `modify_mesh`, `inpaint`, and
    the OpenSCAD actions stay on RunPod for now.
    """

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        # e.g. "https://ppokhrel1--curfdai-ml-web.modal.run"
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def start_raw_job(
        self,
        input_payload: dict[str, Any],
        sync: bool = False,  # noqa: ARG002 — accepted for parity, ignored
    ) -> dict[str, Any]:
        """Spawn a background HTTP call to Modal; return a job id.

        `sync` is accepted but ignored — Modal's HTTP endpoint is always
        synchronous from the caller's POV; we simulate async via the
        background task pattern.
        """
        action = input_payload.get("action")
        if action != "image_to_3d":
            raise NotImplementedError(
                f"ModalClient only handles action='image_to_3d', got {action!r}. "
                "Use the RunPod client for other actions."
            )

        # Drop the `action` key (URL path disambiguates) AND filter out
        # None values so Pydantic on the Modal side falls back to field
        # defaults instead of rejecting with 422. The backend payload
        # often has prompt=None / output_format=None when the request
        # left them unspecified — RunPod's handler was forgiving about
        # this, Modal's strict Pydantic schema isn't.
        body = {
            k: v
            for k, v in input_payload.items()
            if k != "action" and v is not None
        }

        job_id = f"modal-{uuid.uuid4().hex[:16]}"
        _CALLS[job_id] = asyncio.create_task(
            _post_image_to_3d(self._base_url, body, job_id, self._api_key)
        )
        return {"id": job_id, "status": "IN_QUEUE"}

    async def get_status(self, job_id: str) -> dict[str, Any]:
        task = _CALLS.get(job_id)
        if task is None:
            return _status_failed(
                job_id, f"unknown job_id {job_id} (lost across backend restart?)"
            )
        if not task.done():
            return _status_in_progress(job_id)
        # Task completed — pop from registry so we don't leak.
        _CALLS.pop(job_id, None)
        if task.cancelled():
            return _status_failed(job_id, "task cancelled")
        exc = task.exception()
        if exc is not None:
            return _status_failed(job_id, f"{type(exc).__name__}: {exc}")
        output = task.result()
        # Modal's response body is the dict we built in
        # Hunyuan3DService.generate_3d_asset (status, download_url,
        # model_url, parts, textured_url, etc.) — already the shape
        # _persist_image_to_3d_asset expects, so we wrap it as-is.
        return _status_completed(job_id, output)


async def _post_image_to_3d(
    base_url: str, body: dict[str, Any], job_id: str, api_key: str | None
) -> dict[str, Any]:
    """Long-running HTTP call to the Modal /image_to_3d endpoint.

    `follow_redirects=True` is critical: Modal's HTTP gateway returns
    HTTP 303 See Other for any request that exceeds ~150 s, redirecting
    to a polling URL that holds open until the result is ready. Our
    image_to_3d generations take 3-5 min, so every successful request
    hits this redirect. Without follow_redirects we'd see the empty 303
    body and trip JSONDecodeError on every real generation.
    """
    url = f"{base_url}/image_to_3d"
    logger.info(
        f"[modal] POST {url} (job {job_id}) fields={list(body.keys())}"
    )
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # 15 min timeout matches the Worker's modal timeout=900.
    timeout = httpx.Timeout(connect=30.0, read=900.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True
    ) as client:
        response = await client.post(url, json=body, headers=headers)
        if response.status_code >= 400:
            # Surface the FastAPI/Pydantic error body in the exception
            # message so callers see which field tripped the 422, not
            # just a generic "Unprocessable Entity".
            detail = response.text[:1000]
            raise httpx.HTTPStatusError(
                f"{response.status_code} from Modal /image_to_3d: {detail}",
                request=response.request,
                response=response,
            )
        return response.json()


# ─── Factory ──────────────────────────────────────────────────────────────
def get_image_to_3d_modal_client() -> ModalClient:
    from app.core.config import settings
    if not settings.modal_image_to_3d_url:
        raise ValueError(
            "MODAL_IMAGE_TO_3D_URL is not configured "
            "(expected e.g. https://ppokhrel1--curfdai-ml-web.modal.run)"
        )
    return ModalClient(
        settings.modal_image_to_3d_url,
        api_key=settings.modal_image_to_3d_api_key,
    )
