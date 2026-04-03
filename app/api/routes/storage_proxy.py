"""Proxy endpoint to serve files from Supabase Storage (private buckets)."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{bucket}/{file_path:path}")
async def proxy_storage_file(
    bucket: str = Path(...),
    file_path: str = Path(...),
):
    """Download a file from Supabase Storage and stream it to the client."""
    supabase_url = settings.supabase_url
    service_key = settings.supabase_service_role_key or settings.supabase_anon_key

    if not supabase_url or not service_key:
        raise HTTPException(status_code=500, detail="Supabase not configured")

    # Normalize URL — supabase_url may or may not have https:// prefix
    base = supabase_url.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"

    download_url = f"{base}/storage/v1/object/{bucket}/{file_path}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                download_url,
                headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
                follow_redirects=True,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(f"Supabase storage error: {exc.response.status_code} for {download_url}")
        raise HTTPException(status_code=exc.response.status_code, detail="File not found")
    except Exception as exc:
        logger.error(f"Storage proxy error: {exc}")
        raise HTTPException(status_code=502, detail="Failed to fetch file from storage")

    content_type = resp.headers.get("content-type", "application/octet-stream")

    return StreamingResponse(
        iter([resp.content]),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_path.split("/")[-1]}"',
            "Cache-Control": "public, max-age=3600",
        },
    )
