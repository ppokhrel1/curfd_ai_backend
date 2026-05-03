"""Hole-fill / make-watertight a mesh so it slices and prints correctly.

Hunyuan3D-2mini's raw output is often non-watertight — holes, flipped
normals, non-manifold edges — which slicers handle by leaving the
shape unfilled (hollow surfaces with no infill). This endpoint takes
a mesh URL, repairs it on the backend (no GPU needed), uploads the
filled version back to R2, and returns the new URL.

Tries pymeshfix (purpose-built for 3D-printable repair) first, falls
back to trimesh's built-in `fill_holes` + `fix_normals` if pymeshfix
isn't installed.
"""

import io
import logging
import uuid

import trimesh
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes.storage_proxy import fetch_object_bytes, _get_r2_client
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class FillMeshRequest(BaseModel):
    url: str  # source mesh URL (anywhere fetch_object_bytes can resolve)
    method: str = "auto"  # "auto" | "pymeshfix" | "trimesh"


class FillMeshResponse(BaseModel):
    url: str
    is_watertight: bool
    face_count: int
    vertex_count: int
    method: str  # which repair method actually ran


def _detect_ext(url: str) -> str:
    clean = url.split("?")[0]
    ext = clean.rsplit(".", 1)[-1].lower() if "." in clean else "glb"
    return ext if ext in ("glb", "gltf", "stl", "obj") else "glb"


def _to_single_trimesh(loaded) -> trimesh.base.Trimesh:
    """Coerce a trimesh load result (Scene or Trimesh) into a single Trimesh."""
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.base.Trimesh)]
        if not meshes:
            raise ValueError("Scene has no Trimesh geometries")
        return trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    if isinstance(loaded, trimesh.base.Trimesh):
        return loaded
    raise ValueError(f"Unsupported mesh type: {type(loaded)}")


def _repair_pymeshfix(mesh: trimesh.base.Trimesh) -> trimesh.base.Trimesh:
    import pymeshfix
    v, f = pymeshfix.clean_from_arrays(mesh.vertices, mesh.faces)
    return trimesh.Trimesh(vertices=v, faces=f, process=False)


def _repair_trimesh(mesh: trimesh.base.Trimesh) -> trimesh.base.Trimesh:
    # Operate on a copy so we don't mutate caller state.
    m = mesh.copy()
    try:
        m.fill_holes()
    except Exception as e:
        logger.warning(f"trimesh.fill_holes failed: {e}")
    try:
        m.fix_normals()
    except Exception as e:
        logger.warning(f"trimesh.fix_normals failed: {e}")
    try:
        m.process(validate=True)
    except Exception as e:
        logger.warning(f"trimesh.process failed: {e}")
    return m


@router.post("/fill", response_model=FillMeshResponse)
async def fill_mesh(req: FillMeshRequest):
    """Download the mesh at `req.url`, hole-fill it, upload the result to R2."""
    # 1. Fetch the source bytes via the storage proxy (R2-first, B2-fallback).
    try:
        source_bytes, _ = await fetch_object_bytes(req.url)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to fetch source mesh: {exc}")
        raise HTTPException(status_code=502, detail="Failed to fetch source mesh")

    # 2. Load with trimesh, force-flatten Scene → Trimesh.
    ext = _detect_ext(req.url)
    try:
        loaded = trimesh.load(io.BytesIO(source_bytes), file_type=ext)
        mesh = _to_single_trimesh(loaded)
    except Exception as exc:
        logger.error(f"Failed to parse mesh ({ext}): {exc}")
        raise HTTPException(status_code=422, detail=f"Could not parse mesh: {exc}")

    if len(mesh.faces) == 0:
        raise HTTPException(status_code=422, detail="Mesh has no faces")

    # 3. Repair.
    method_used = req.method
    if req.method in ("auto", "pymeshfix"):
        try:
            mesh = _repair_pymeshfix(mesh)
            method_used = "pymeshfix"
            logger.info(f"Filled via pymeshfix: faces={len(mesh.faces)} watertight={mesh.is_watertight}")
        except ImportError:
            if req.method == "pymeshfix":
                raise HTTPException(
                    status_code=503,
                    detail="pymeshfix not installed on server; pass method='trimesh' or install pymeshfix",
                )
            # auto → fall through to trimesh
            mesh = _repair_trimesh(mesh)
            method_used = "trimesh"
        except Exception as e:
            logger.warning(f"pymeshfix failed, falling back to trimesh: {e}")
            mesh = _repair_trimesh(mesh)
            method_used = "trimesh"
    else:  # explicit "trimesh"
        mesh = _repair_trimesh(mesh)
        method_used = "trimesh"

    # 4. Export to GLB and upload to R2.
    try:
        out_bytes = mesh.export(file_type="glb")
    except Exception as exc:
        logger.error(f"GLB export failed: {exc}")
        raise HTTPException(status_code=500, detail=f"GLB export failed: {exc}")

    client = _get_r2_client()
    if client is None or not settings.r2_bucket_name:
        raise HTTPException(status_code=500, detail="R2 not configured on this server")

    new_key = f"filled_models/filled_{uuid.uuid4().hex[:12]}.glb"
    try:
        client.put_object(
            Bucket=settings.r2_bucket_name,
            Key=new_key,
            Body=out_bytes,
            ContentType="model/gltf-binary",
        )
    except Exception as exc:
        logger.error(f"R2 upload failed: {exc}")
        raise HTTPException(status_code=502, detail="Failed to upload filled mesh")

    new_url = (
        f"https://{settings.r2_account_id}.r2.cloudflarestorage.com/"
        f"{settings.r2_bucket_name}/{new_key}"
    )
    return FillMeshResponse(
        url=new_url,
        is_watertight=bool(mesh.is_watertight),
        face_count=int(len(mesh.faces)),
        vertex_count=int(len(mesh.vertices)),
        method=method_used,
    )
