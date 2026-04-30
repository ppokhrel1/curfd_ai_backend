"""Proxy endpoint to serve files from Backblaze B2 object storage."""

import base64
import logging

import httpx
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level B2 auth cache
_b2_auth_token: str | None = None
_b2_api_url: str | None = None
_b2_download_url: str | None = None


def _ensure_b2_auth(force: bool = False) -> None:
    global _b2_auth_token, _b2_api_url, _b2_download_url
    if _b2_auth_token and not force:
        return
    key_id = settings.b2_key_id
    app_key = settings.b2_application_key
    if not key_id or not app_key:
        raise HTTPException(status_code=500, detail="B2 credentials not configured")
    auth_string = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    resp = httpx.get(
        "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
        headers={"Authorization": f"Basic {auth_string}"},
        timeout=15,
    )
    if not resp.is_success:
        logger.error(f"B2 auth failed: {resp.status_code} {resp.text[:200]}")
        raise HTTPException(status_code=502, detail="B2 auth failed")
    data = resp.json()
    _b2_auth_token = data["authorizationToken"]
    _b2_api_url = data["apiUrl"]
    _b2_download_url = data["downloadUrl"]
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


@router.get("/{bucket}/{file_path:path}")
async def proxy_storage_file(
    bucket: str = Path(...),
    file_path: str = Path(...),
):
    """Download a file from B2 and stream it to the client."""
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
            logger.info(f"[B2 proxy] First attempt: {resp.status_code}")

            # Token expired or unauthorized — re-auth and retry once
            if resp.status_code in (401, 403):
                logger.warning(f"[B2 proxy] Got {resp.status_code}, re-authenticating...")
                _ensure_b2_auth(force=True)
                resp = await _download_from_b2(client, bucket_name, file_path)
                logger.info(f"[B2 proxy] Retry: {resp.status_code}")

            if not resp.is_success:
                logger.error(f"[B2 proxy] Failed: {resp.status_code} body={resp.text[:500]}")
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"B2 download failed ({resp.status_code}): {resp.text[:200]}",
                )
    except HTTPException:
        raise
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
