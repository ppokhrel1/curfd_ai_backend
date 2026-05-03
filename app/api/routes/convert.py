"""Mesh format conversion endpoints (GLB → STL, etc.)."""

import io
import logging

import trimesh
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.routes.storage_proxy import fetch_object_bytes

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stl")
async def convert_to_stl(
    url: str = Query(..., description="Storage URL or path of the GLB/OBJ file to convert"),
):
    """Download a mesh from storage, convert to STL, and stream it back.

    Use this to get slicer-compatible files (Anycubic, Cura, PrusaSlicer).
    """
    try:
        source_bytes, _ = await fetch_object_bytes(url)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to fetch source file for conversion: {exc}")
        raise HTTPException(status_code=502, detail="Failed to download source file")

    # Detect format from URL
    clean_url = url.split("?")[0]
    ext = clean_url.rsplit(".", 1)[-1].lower() if "." in clean_url else "glb"

    # Load with trimesh — try without force first (handles Scene GLBs)
    try:
        loaded = trimesh.load(io.BytesIO(source_bytes), file_type=ext)
        if isinstance(loaded, trimesh.Scene):
            meshes = list(loaded.dump())
            if not meshes:
                raise ValueError("Scene contains no meshes")
            loaded = trimesh.util.concatenate(meshes)
        if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
            raise ValueError(f"Loaded object has no faces: {type(loaded)}")
        logger.info(f"Loaded mesh: {len(loaded.faces)} faces, {len(loaded.vertices)} vertices")
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
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{stl_filename}"',
            "Content-Length": str(len(stl_bytes)),
        },
    )
