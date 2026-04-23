"""Mesh format conversion endpoints (GLB → STL, etc.)."""

import io
import logging

import httpx
import trimesh
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.api.routes import storage_proxy

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_b2_url(url: str) -> tuple[str, dict]:
    """Resolve a B2 file URL and return (download_url, auth_headers)."""
    storage_proxy._ensure_b2_auth()
    headers = {"Authorization": storage_proxy._b2_auth_token}

    # If it's already a full B2 URL, use it directly
    if "backblazeb2.com" in url:
        return url, headers

    # If it's a relative path like "generated_models/foo.glb", build the full URL
    bucket_name = settings.b2_bucket_name
    return f"{storage_proxy._b2_download_url}/file/{bucket_name}/{url}", headers


@router.get("/stl")
async def convert_to_stl(
    url: str = Query(..., description="B2 URL or path of the GLB/OBJ file to convert"),
):
    """Download a mesh from B2, convert to STL, and stream it back.

    Use this to get slicer-compatible files (Anycubic, Cura, PrusaSlicer).
    """
    # Resolve URL
    try:
        download_url, headers = _resolve_b2_url(url)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"B2 auth failed for conversion: {e}")
        raise HTTPException(status_code=502, detail="Storage auth failed")

    # Download the mesh
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(download_url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Source file not found")
    except Exception as exc:
        logger.error(f"Failed to download mesh for conversion: {exc}")
        raise HTTPException(status_code=502, detail="Failed to download source file")

    # Detect format from URL
    clean_url = url.split("?")[0]
    ext = clean_url.rsplit(".", 1)[-1].lower() if "." in clean_url else "glb"

    # Load with trimesh
    try:
        loaded = trimesh.load(
            io.BytesIO(resp.content),
            file_type=ext,
            force="mesh",
        )
        if isinstance(loaded, trimesh.Scene):
            loaded = trimesh.util.concatenate(loaded.dump())
    except Exception as e:
        logger.error(f"Failed to load mesh for conversion: {e}")
        raise HTTPException(status_code=422, detail=f"Could not parse mesh: {e}")

    # Export as STL
    try:
        stl_bytes = loaded.export(file_type="stl")
    except Exception as e:
        logger.error(f"STL export failed: {e}")
        raise HTTPException(status_code=500, detail=f"STL export failed: {e}")

    # Derive filename
    src_name = clean_url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "/" in clean_url else "model"
    stl_filename = f"{src_name}.stl"

    logger.info(f"Converted {ext} → STL ({len(stl_bytes)} bytes): {stl_filename}")

    return StreamingResponse(
        io.BytesIO(stl_bytes),
        media_type="model/stl",
        headers={
            "Content-Disposition": f'attachment; filename="{stl_filename}"',
            "Content-Length": str(len(stl_bytes)),
        },
    )
