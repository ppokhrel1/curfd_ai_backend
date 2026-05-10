"""Mesh format conversion endpoints (GLB → STL / 3MF, etc.)."""

import io
import logging
import time

import numpy as np
import trimesh
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.routes.storage_proxy import fetch_object_bytes

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_single_mesh(source_bytes: bytes, ext: str) -> trimesh.Trimesh:
    """Parse raw GLB/OBJ/STL bytes into one Trimesh (flattens Scene)."""
    loaded = trimesh.load(io.BytesIO(source_bytes), file_type=ext)
    if isinstance(loaded, trimesh.Scene):
        meshes = list(loaded.dump())
        if not meshes:
            raise ValueError("Scene contains no meshes")
        loaded = trimesh.util.concatenate(meshes)
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError(f"Loaded object has no faces: {type(loaded)}")
    return loaded


def _make_print_ready(
    mesh: trimesh.Trimesh,
    target_size_mm: float | None,
    decimate_to: int | None,
    do_repair: bool,
    do_center: bool,
) -> trimesh.Trimesh:
    """Light-weight prep so a Hunyuan3D output drops cleanly into a slicer.

    Stays inside a ~5 s budget: trimesh-only repair (fill_holes +
    fix_normals + process(validate=True)) — no pymeshfix, no smoothing,
    no UV work. For deeper repair the chat has a separate "Fill for
    printing" button that calls the heavier mesh_fill service.

    Operations in order so each step sees the cheaper mesh:
      1. Decimate (if face count exceeds `decimate_to`) — slicers don't
         care about >200k faces and the printer can't print at that
         resolution anyway; fewer faces = faster slicing.
      2. Hole-fill + normal repair — meshes from AI generators often
         leak; un-filled slicers leave them as hollow shells which
         destroys infill.
      3. Recenter at origin — slicer build-plate origin is (0,0,0).
      4. Scale to target mm — Hunyuan output is unit-normalised
         (~2 units across); without scale the slicer sees a 2 mm
         model and asks "did you mean mm?" or just places a speck.
    """
    if decimate_to and len(mesh.faces) > decimate_to:
        t = time.time()
        try:
            import fast_simplification
            v_in = np.ascontiguousarray(mesh.vertices, dtype=np.float32)
            f_in = np.ascontiguousarray(mesh.faces, dtype=np.uint32)
            target_reduction = 1.0 - (decimate_to / len(mesh.faces))
            v_out, f_out = fast_simplification.simplify(
                v_in, f_in, target_reduction=target_reduction
            )
            mesh = trimesh.Trimesh(vertices=v_out, faces=f_out, process=False)
            logger.info(
                f"[convert] decimated {len(f_in):,} → {len(f_out):,} faces "
                f"({time.time() - t:.2f}s)"
            )
        except ImportError:
            # fast_simplification not installed — fall back to trimesh's
            # quadric decimation. Slower but still well under budget.
            mesh = mesh.simplify_quadric_decimation(decimate_to)
            logger.info(
                f"[convert] decimated via trimesh ({time.time() - t:.2f}s)"
            )

    if do_repair:
        t = time.time()
        try:
            mesh.fill_holes()
        except Exception as e:
            logger.warning(f"[convert] fill_holes failed: {e}")
        try:
            mesh.fix_normals()
        except Exception as e:
            logger.warning(f"[convert] fix_normals failed: {e}")
        try:
            mesh.process(validate=True)
        except Exception as e:
            logger.warning(f"[convert] process(validate=True) failed: {e}")
        logger.info(
            f"[convert] repair: faces={len(mesh.faces):,} "
            f"watertight={mesh.is_watertight} ({time.time() - t:.2f}s)"
        )

    if do_center:
        # Centre on origin and seat on z=0 so the slicer drops it on
        # the build plate without "object is below build plate" warnings.
        bbox = mesh.bounding_box.bounds
        translation = -((bbox[0] + bbox[1]) / 2.0)
        # Lift to z=0 instead of centering on z.
        translation[2] = -bbox[0][2]
        mesh.apply_translation(translation)

    if target_size_mm and target_size_mm > 0:
        bbox = mesh.bounding_box.extents
        longest = float(np.max(bbox))
        if longest > 0:
            scale = float(target_size_mm) / longest
            mesh.apply_scale(scale)
            logger.info(
                f"[convert] scaled by {scale:.4f}× "
                f"(longest dim {longest:.3f} → {target_size_mm} mm)"
            )

    return mesh


@router.get("/stl")
async def convert_to_stl(
    url: str = Query(..., description="Storage URL or path of the GLB/OBJ file to convert"),
    repair: bool = Query(True, description="Run trimesh fill_holes + fix_normals (slicer-safety)"),
    center: bool = Query(True, description="Re-centre at origin, seat on z=0"),
    target_size_mm: float | None = Query(
        80.0,
        ge=1.0,
        le=400.0,
        description="Scale so the longest bbox dim equals this in millimetres "
        "(default 80 mm — fits an Anycubic Kobra 220 × 220 build plate "
        "with margin). Pass 0 to skip scaling.",
    ),
    decimate_to: int | None = Query(
        200_000,
        ge=10_000,
        le=2_000_000,
        description="Cap face count via fast_simplification before export. "
        "200k is more than enough for FDM printing.",
    ),
    fmt: str = Query("stl", description="Output format: 'stl' or '3mf'"),
):
    """Download a mesh from storage, prep it for printing, return STL / 3MF.

    Defaults are tuned for Anycubic Slicer Next on a Kobra-class FDM
    printer: 80 mm longest dim, decimated to 200k faces, hole-filled.
    Pass query params to override any step.
    """
    fmt = fmt.lower()
    if fmt not in ("stl", "3mf"):
        raise HTTPException(
            status_code=422, detail="fmt must be 'stl' or '3mf'"
        )

    try:
        source_bytes, _ = await fetch_object_bytes(url)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to fetch source file for conversion: {exc}")
        raise HTTPException(status_code=502, detail="Failed to download source file")

    # Detect format from URL
    clean_url = url.split("?")[0]
    src_ext = clean_url.rsplit(".", 1)[-1].lower() if "." in clean_url else "glb"

    try:
        mesh = _load_single_mesh(source_bytes, src_ext)
        logger.info(
            f"[convert] loaded {src_ext}: {len(mesh.faces):,} faces, "
            f"{len(mesh.vertices):,} verts"
        )
    except Exception as e:
        logger.error(f"Failed to load mesh for conversion: {e}")
        raise HTTPException(status_code=422, detail=f"Could not parse mesh: {e}")

    # Total prep budget ~5 s for default sizes.
    t_prep = time.time()
    mesh = _make_print_ready(
        mesh,
        target_size_mm=target_size_mm if target_size_mm and target_size_mm > 0 else None,
        decimate_to=decimate_to,
        do_repair=repair,
        do_center=center,
    )
    logger.info(f"[convert] prep total: {time.time() - t_prep:.2f}s")

    # Export — STL or 3MF
    try:
        out_bytes = mesh.export(file_type=fmt)
    except Exception as e:
        logger.error(f"{fmt.upper()} export failed: {e}")
        raise HTTPException(status_code=500, detail=f"{fmt.upper()} export failed: {e}")

    # Derive filename. If we prepped (default), tag the filename so the
    # user can tell at a glance vs the raw original.
    src_name = clean_url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "/" in clean_url else "model"
    tag = "_printready" if (repair or center or target_size_mm) else ""
    out_filename = f"{src_name}{tag}.{fmt}"

    logger.info(
        f"[convert] {src_ext} → {fmt} {out_filename} "
        f"({len(out_bytes):,} bytes, {len(mesh.faces):,} faces)"
    )

    return StreamingResponse(
        io.BytesIO(out_bytes),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{out_filename}"',
            "Content-Length": str(len(out_bytes)),
        },
    )
