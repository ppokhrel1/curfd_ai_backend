"""3MF writer for OrcaSlicer / Bambu Studio / Anycubic Slicer.

Writes a single watertight mesh as a plain 3MF. **Auto-painting is
intentionally disabled** for now:

  - Per-triangle `paint_color` uses Slic3r's packed split-tree
    encoding; emitting a wrong byte segfaults the slicer
    (`TriangleSelector::perform_split` walked past buffer end).
  - Per-cluster sub-objects with `<metadata name="extruder">N</metadata>`
    loaded cleanly but the slicer ignored the metadata (the real key
    lives in a `Metadata/Slic3r_PE_model.config` sidecar whose schema
    varies subtly across forks) AND introduced non-manifold edges at
    cluster boundaries.

So we ship the safe path: one solid mesh, user paints colours in the
slicer's built-in MMU paint tool. K-means + palette helpers remain
below so an opt-in auto-paint mode can be re-added once we've nailed
the bit-packing for a specific slicer.
"""

from __future__ import annotations

import io
import logging
import zipfile
from xml.sax.saxutils import escape

import numpy as np
import trimesh

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────
# AMS Lite = 4 slots, AMS HT = up to 16. Cap user input to 16.
MAX_FILAMENTS = 16


# ─── Texture sampling ──────────────────────────────────────────────────────
def _sample_face_colors(mesh: trimesh.Trimesh) -> np.ndarray | None:
    """Return (n_faces, 3) RGB array sampled at each face centroid.

    Returns None if the mesh has no baseColorTexture / uv coords.
    """
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None
    uv = getattr(visual, "uv", None)
    material = getattr(visual, "material", None)
    if uv is None or material is None:
        return None
    texture = getattr(material, "baseColorTexture", None)
    if texture is None:
        # Some PBR materials nest the image under .image
        texture = getattr(material, "image", None)
    if texture is None:
        return None

    # PIL.Image -> np.uint8 (H, W, 3)
    tex = np.array(texture.convert("RGB"))
    th, tw = tex.shape[:2]
    if th == 0 or tw == 0:
        return None

    # Per-face UV centroid: average of the three vertex UVs.
    faces = np.asarray(mesh.faces, dtype=np.int64)
    uv_arr = np.asarray(uv, dtype=np.float32)
    face_uv = uv_arr[faces].mean(axis=1)  # (n_faces, 2)

    # GLTF UV origin is top-left, but Pillow stores top-down too; the
    # convention that "wins" for textured GLBs is u→x, (1-v)→y so the
    # texture appears right-side-up on the mesh.
    u = np.clip(face_uv[:, 0], 0.0, 1.0)
    v = np.clip(1.0 - face_uv[:, 1], 0.0, 1.0)
    px = np.clip((u * (tw - 1)).astype(np.int64), 0, tw - 1)
    py = np.clip((v * (th - 1)).astype(np.int64), 0, th - 1)

    return tex[py, px].astype(np.float32)  # (n_faces, 3)


# ─── K-means (numpy, no sklearn) ───────────────────────────────────────────
def _kmeans(
    samples: np.ndarray,
    k: int,
    max_iter: int = 32,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Tiny k-means. Returns (labels[n], centroids[k, d]).

    Uses k-means++ init so 8-16 cluster runs converge in ~10 iters on
    RGB samples without sklearn.
    """
    n, d = samples.shape
    k = min(k, n)
    rng = np.random.default_rng(seed)

    # k-means++ init
    centroids = np.empty((k, d), dtype=np.float32)
    centroids[0] = samples[rng.integers(n)]
    dist_sq = np.full(n, np.inf, dtype=np.float32)
    for i in range(1, k):
        diff = samples - centroids[i - 1]
        dist_sq = np.minimum(dist_sq, np.einsum("ij,ij->i", diff, diff))
        probs = dist_sq / max(dist_sq.sum(), 1e-9)
        idx = rng.choice(n, p=probs)
        centroids[i] = samples[idx]

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iter):
        # Assign — squared distance to each centroid
        d2 = (
            (samples[:, None, :] - centroids[None, :, :]) ** 2
        ).sum(axis=2)
        new_labels = d2.argmin(axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(k):
            mask = labels == c
            if mask.any():
                centroids[c] = samples[mask].mean(axis=0)

    return labels, centroids


def _kmeans_face_labels(
    face_rgb: np.ndarray,
    k: int,
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Cluster per-face RGB into k labels. Returns (labels, palette_rgb)."""
    k = max(1, min(k, MAX_FILAMENTS))
    labels, centroids = _kmeans(face_rgb, k)
    palette = [
        (int(c[0]), int(c[1]), int(c[2])) for c in np.clip(centroids, 0, 255)
    ]
    return labels, palette


# ─── 3MF XML writer ────────────────────────────────────────────────────────
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)


def _write_vertices(buf: io.StringIO, vertices: np.ndarray) -> None:
    """6 dp is well below FDM nozzle precision (~0.4 mm)."""
    buf.write("<vertices>")
    for x, y, z in vertices:
        buf.write(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>')
    buf.write("</vertices>")


def _write_triangles(buf: io.StringIO, faces: np.ndarray) -> None:
    buf.write("<triangles>")
    for v1, v2, v3 in faces:
        buf.write(
            f'<triangle v1="{int(v1)}" v2="{int(v2)}" v3="{int(v3)}"/>'
        )
    buf.write("</triangles>")


def _build_model_xml(
    vertices: np.ndarray,
    faces: np.ndarray,
    palette: list[tuple[int, int, int]] | None,
    object_name: str,
) -> str:
    """Build 3D/3dmodel.model XML body — one plain object, single mesh.

    The cluster palette (if any) is surfaced as `<metadata>` for
    downstream tools; slicers ignore unknown metadata, so this never
    affects load.
    """
    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    parts.append(
        '<model unit="millimeter" '
        'xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
    )
    parts.append('<metadata name="Application">curfd_ai</metadata>')
    if palette:
        hex_palette = ",".join(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in palette)
        parts.append(
            f'<metadata name="curfd_ai:palette">{escape(hex_palette)}</metadata>'
        )

    parts.append("<resources>")
    parts.append(
        f'<object id="1" type="model" name="{escape(object_name)}"><mesh>'
    )
    buf = io.StringIO()
    _write_vertices(buf, vertices)
    _write_triangles(buf, faces)
    parts.append(buf.getvalue())
    parts.append("</mesh></object>")
    parts.append("</resources>")
    parts.append('<build><item objectid="1"/></build>')
    parts.append("</model>")
    return "".join(parts)


def write_painted_3mf(
    mesh: trimesh.Trimesh,
    face_labels: np.ndarray | None,  # noqa: ARG001 — kept for API stability
    palette: list[tuple[int, int, int]] | None,
    object_name: str = "model",
) -> bytes:
    """Pack a plain 3MF zip. `face_labels` is accepted but unused — see
    module docstring for why auto-paint is disabled."""
    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.int64)

    model_xml = _build_model_xml(vertices, faces, palette, object_name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", model_xml)
    return buf.getvalue()


# ─── High-level orchestration ──────────────────────────────────────────────
def derive_face_labels(
    mesh: trimesh.Trimesh,
    filaments: int,
    part_face_ids: list[list[int]] | None = None,
) -> tuple[np.ndarray | None, list[tuple[int, int, int]] | None]:
    """Choose the best face → filament mapping strategy.

    Priority:
      1. Texture present → K-means cluster per-face RGB samples
      2. Part face IDs provided → one filament per part (cycled if
         parts > filaments)
      3. Neither → return (None, None) for a plain single-filament 3MF
    """
    n_faces = len(mesh.faces)

    # 1. Textured path
    face_rgb = _sample_face_colors(mesh)
    if face_rgb is not None and face_rgb.shape[0] == n_faces:
        labels, palette = _kmeans_face_labels(face_rgb, filaments)
        logger.info(
            f"[3mf] textured: clustered {n_faces:,} faces into "
            f"{len(palette)} filament slots"
        )
        return labels, palette

    # 2. Part-segmented path
    if part_face_ids:
        labels = np.zeros(n_faces, dtype=np.int32)
        # Cycle filament indices 0..filaments-1 across parts so any
        # number of parts maps to a bounded slot count.
        slot_count = max(1, min(filaments, MAX_FILAMENTS))
        for part_idx, ids in enumerate(part_face_ids):
            ids_arr = np.asarray(ids, dtype=np.int64)
            ids_arr = ids_arr[(ids_arr >= 0) & (ids_arr < n_faces)]
            labels[ids_arr] = part_idx % slot_count
        # No real RGB palette — emit evenly-spaced hues so a downstream
        # tool can pick something reasonable.
        palette = [
            tuple(
                int(c * 255)
                for c in _hsv_to_rgb((i / max(1, slot_count)) % 1.0, 0.65, 0.95)
            )
            for i in range(slot_count)
        ]
        logger.info(
            f"[3mf] part-segmented: {len(part_face_ids)} parts → "
            f"{slot_count} filament slots"
        )
        return labels, palette  # type: ignore[return-value]

    # 3. Plain
    logger.info("[3mf] plain (no texture, no parts) — single filament")
    return None, None


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    """Tiny HSV→RGB so we don't pull in colorsys."""
    import colorsys
    return colorsys.hsv_to_rgb(h, s, v)
