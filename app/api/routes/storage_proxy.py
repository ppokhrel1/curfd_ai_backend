"""Proxy endpoint to serve files from Backblaze B2 object storage."""

import base64
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# B2 tokens are valid 24h; refresh proactively at 23h.
_B2_AUTH_TTL_SECONDS = 23 * 60 * 60

_b2_auth_token: str | None = None
_b2_api_url: str | None = None
_b2_download_url: str | None = None
_b2_auth_expires_at: float = 0.0


def _ensure_b2_auth(force: bool = False) -> None:
    global _b2_auth_token, _b2_api_url, _b2_download_url, _b2_auth_expires_at
    if _b2_auth_token and not force and time.monotonic() < _b2_auth_expires_at:
        return
    key_id = settings.b2_key_id
    app_key = settings.b2_application_key
    if not key_id or not app_key:
        raise HTTPException(status_code=500, detail="B2 credentials not configured")
    auth_string = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    try:
        resp = httpx.get(
            "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
            headers={"Authorization": f"Basic {auth_string}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        logger.error(f"B2 auth network error: {exc}")
        raise HTTPException(status_code=502, detail="B2 auth network error")
    if not resp.is_success:
        logger.error(f"B2 auth failed: {resp.status_code} {resp.text[:200]}")
        raise HTTPException(status_code=502, detail="B2 auth failed")
    data = resp.json()
    _b2_auth_token = data["authorizationToken"]
    _b2_api_url = data["apiUrl"]
    _b2_download_url = data["downloadUrl"]
    _b2_auth_expires_at = time.monotonic() + _B2_AUTH_TTL_SECONDS
    allowed = data.get("allowed", {})
    logger.info(
        f"B2 auth OK — downloadUrl={_b2_download_url} "
        f"bucketId={allowed.get('bucketId')} "
        f"capabilities={allowed.get('capabilities')} "
        f"namePrefix={allowed.get('namePrefix')}"
    )


async def _download_from_b2(client: httpx.AsyncClient, bucket_name: str, file_path: str) -> httpx.Response:
    """Download a file from B2 using the /file/ friendly URL with auth."""
    download_url = f"{_b2_download_url}/file/{bucket_name}/{file_path}"
    resp = await client.get(
        download_url,
        headers={"Authorization": _b2_auth_token},
        follow_redirects=True,
    )
    return resp


async def _diagnose_404(client: httpx.AsyncClient, bucket_name: str, file_path: str) -> None:
    """Log what files actually exist near the requested path. Aids debugging
    when the file is genuinely there but under a slightly different key."""
    if not settings.b2_bucket_id:
        logger.warning("[B2 proxy] Cannot diagnose 404 — B2_BUCKET_ID not configured")
        return
    prefix = "/".join(file_path.split("/")[:-1])
    if prefix:
        prefix += "/"
    try:
        resp = await client.post(
            f"{_b2_api_url}/b2api/v3/b2_list_file_names",
            json={"bucketId": settings.b2_bucket_id, "prefix": prefix, "maxFileCount": 20},
            headers={"Authorization": _b2_auth_token},
        )
        if resp.is_success:
            names = [f.get("fileName") for f in resp.json().get("files", [])]
            logger.warning(
                f"[B2 proxy] 404 diagnosis — bucket={bucket_name} prefix={prefix!r} "
                f"requested={file_path!r} found={names}"
            )
        else:
            logger.warning(f"[B2 proxy] 404 diagnosis list failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        logger.warning(f"[B2 proxy] 404 diagnosis error: {exc}")


@router.get("/debug/b2-auth")
async def debug_b2_auth():
    """Temporary debug endpoint to verify B2 credentials and capabilities."""
    try:
        _ensure_b2_auth(force=True)
    except HTTPException as e:
        return {"error": str(e.detail), "status": "auth_failed"}
    return {
        "status": "ok",
        "download_url": _b2_download_url,
        "has_token": bool(_b2_auth_token),
        "token_prefix": _b2_auth_token[:20] + "..." if _b2_auth_token else None,
    }


@router.get("/{bucket}/{file_path:path}")
async def proxy_storage_file(
    bucket: str = Path(...),
    file_path: str = Path(...),
):
    """Download a file from B2 and stream it to the client.

    Error semantics (so the frontend can decide how to recover):
      - 404 → file genuinely missing in B2 (don't retry; fall back to compile)
      - 502 → auth, network, or other proxy failure (retryable)
    """
    logger.info(f"[B2 proxy] Request: bucket={bucket} file_path={file_path}")

    try:
        _ensure_b2_auth()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"B2 auth error: {e}")
        raise HTTPException(status_code=502, detail="B2 auth failed")

    bucket_name = settings.b2_bucket_name or bucket
    if settings.b2_bucket_name and bucket != settings.b2_bucket_name:
        file_path = f"{bucket}/{file_path}"

    logger.info(f"[B2 proxy] Resolved: bucket_name={bucket_name} file_path={file_path}")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await _download_from_b2(client, bucket_name, file_path)

            # Token expired or unauthorized — re-auth and retry once.
            if resp.status_code in (401, 403):
                logger.warning(f"[B2 proxy] Got {resp.status_code}, re-authenticating...")
                _ensure_b2_auth(force=True)
                resp = await _download_from_b2(client, bucket_name, file_path)

            if resp.status_code == 404:
                # Log what files DO exist at this prefix so path mismatches
                # are obvious without needing a separate B2 client to debug.
                await _diagnose_404(client, bucket_name, file_path)
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {bucket_name}/{file_path}",
                )

            if not resp.is_success:
                logger.error(f"[B2 proxy] Upstream {resp.status_code}: {resp.text[:200]}")
                # Non-404 upstream failures map to 502 — they're transport-layer issues,
                # not client errors. The frontend will retry / surface differently.
                raise HTTPException(
                    status_code=502,
                    detail=f"B2 download failed ({resp.status_code})",
                )
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.error(f"B2 proxy network error: {exc}")
        raise HTTPException(status_code=502, detail="Storage network error")
    except Exception as exc:
        logger.error(f"B2 proxy error: {exc}")
        raise HTTPException(status_code=502, detail="Failed to fetch file from B2")

    content_type = resp.headers.get("content-type", "application/octet-stream")

    return StreamingResponse(
        iter([resp.content]),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_path.split("/")[-1]}"',
            "Cache-Control": "public, max-age=3600",
        },
    )
