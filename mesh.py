"""
mesh.py — turn an elevation grid into a coloured 3D model.

Two meshing strategies, mirroring the tradeoff a tool like this actually faces:

* Adaptive (TIN). A Triangulated Irregular Network via RTIN (see rtin.py).
  Flat ground
  spends few triangles, rugged ground spends many. Light and ideal for viewing
  or web/glTF delivery. Surface only.

* Solid. A full grid surface closed with a flat base and four walls, so the
  result is watertight — the version you'd send to a slicer for 3D printing.

Both are lifted from grid space into a centred, sensibly scaled model space,
then tinted with a hypsometric (elevation) colour ramp — the same ramp the UI
shows as a legend, which doubles as the app's visual signature.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
import trimesh

from PIL import Image

import rtin  # pure-Python RTIN — feature-adaptive TIN with nothing to compile

M_PER_DEG_LAT = 111_320.0
TARGET_SIZE = 200.0       # longest horizontal side, in model units
ROCK_COLOR = np.array([74, 66, 57], dtype=np.uint8)  # base + walls

# Hypsometric ramp: low green → tan → snow. (stop 0..1, RGB)
_RAMP = [
    (0.00, (46, 90, 60)),
    (0.35, (111, 127, 63)),
    (0.60, (176, 138, 78)),
    (0.80, (205, 176, 131)),
    (1.00, (239, 230, 214)),
]


@dataclass
class BuiltMesh:
    mesh: trimesh.Trimesh          # for file export
    positions: np.ndarray          # (N,3) float32, model space
    faces: np.ndarray              # (M,3) int32
    colors: np.ndarray             # (N,3) uint8
    stats: dict
    uvs: np.ndarray | None = None          # (N,2) float32, for texture draping
    texture_png: bytes | None = None       # draped map image, if any


def _ramp_colors(norm: np.ndarray) -> np.ndarray:
    """Map normalised elevation [0,1] through the hypsometric ramp → uint8 RGB."""
    stops = np.array([s for s, _ in _RAMP])
    cols = np.array([c for _, c in _RAMP], dtype=np.float64)
    out = np.zeros((norm.size, 3))
    for ch in range(3):
        out[:, ch] = np.interp(norm, stops, cols[:, ch])
    return out.astype(np.uint8)


def _grid_to_model(cols: np.ndarray, rows: np.ndarray, elev: np.ndarray,
                   shape: tuple[int, int], bbox, exaggeration: float,
                   elev_ref: float, scale: float,
                   width_m: float, height_m: float) -> np.ndarray:
    """Map grid (col, row, metres) → centred model coordinates (X east, Y up, Z south)."""
    H, W = shape
    x = (cols / (W - 1) * width_m - width_m / 2.0) * scale
    z = (rows / (H - 1) * height_m - height_m / 2.0) * scale
    y = (elev - elev_ref) * scale * exaggeration
    return np.column_stack([x, y, z]).astype(np.float32)


def _horizontal_metres(bbox, shape) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    mid = math.radians((min_lat + max_lat) / 2.0)
    width_m = abs(max_lon - min_lon) * M_PER_DEG_LAT * math.cos(mid)
    height_m = abs(max_lat - min_lat) * M_PER_DEG_LAT
    return max(width_m, 1.0), max(height_m, 1.0)


def _adaptive_grid_size(resolution: int) -> int:
    """RTIN needs a (2^k + 1) square grid. Kept modest so meshing stays snappy."""
    return 65 if resolution < 80 else 129


def _build_tin(elev: np.ndarray, bbox, exaggeration: float,
               max_error: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gs = _adaptive_grid_size(max(elev.shape))
    if elev.shape != (gs, gs):
        grid = np.asarray(
            Image.fromarray(elev.astype(np.float32), mode="F").resize((gs, gs), Image.BILINEAR),
            dtype=np.float32,
        )
    else:
        grid = elev.astype(np.float32)

    verts_xy, faces = rtin.get_martini(gs).get_mesh(grid, max_error=max_error)
    cols = verts_xy[:, 0].astype(np.float64)
    rows = verts_xy[:, 1].astype(np.float64)
    heights = grid[verts_xy[:, 1], verts_xy[:, 0]].astype(np.float64)

    shape = (gs, gs)
    width_m, height_m = _horizontal_metres(bbox, shape)
    scale = TARGET_SIZE / max(width_m, height_m)
    elev_ref = float(grid.min())

    positions = _grid_to_model(cols, rows, heights, shape, bbox,
                               exaggeration, elev_ref, scale, width_m, height_m)
    faces = _orient_vertical(positions, faces.astype(np.int32), want_up=True)
    span = float(grid.max() - grid.min()) or 1.0
    colors = _ramp_colors((heights - elev_ref) / span)
    uvs = np.column_stack([cols / (gs - 1), 1.0 - rows / (gs - 1)]).astype(np.float32)
    return positions, faces, colors, uvs


def _face_normals(positions: np.ndarray, faces: np.ndarray) -> np.ndarray:
    v0, v1, v2 = positions[faces[:, 0]], positions[faces[:, 1]], positions[faces[:, 2]]
    return np.cross(v1 - v0, v2 - v0)


def _orient_vertical(positions, faces, want_up: bool) -> np.ndarray:
    """Flip faces whose normal points the wrong way along Y (fast, vectorized)."""
    ny = _face_normals(positions, faces)[:, 1]
    flip = ny < 0 if want_up else ny > 0
    faces[flip] = faces[flip][:, ::-1]
    return faces


def _orient_outward(positions, faces, centroid_xz) -> np.ndarray:
    """Flip wall faces so their horizontal normal points away from the centre."""
    n = _face_normals(positions, faces)[:, [0, 2]]
    centre = positions[faces].mean(axis=1)[:, [0, 2]]
    outward = centre - centroid_xz
    flip = np.einsum("ij,ij->i", n, outward) < 0
    faces[flip] = faces[flip][:, ::-1]
    return faces


def _build_solid(elev: np.ndarray, bbox, exaggeration: float,
                 base_units: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    H, W = elev.shape
    width_m, height_m = _horizontal_metres(bbox, elev.shape)
    scale = TARGET_SIZE / max(width_m, height_m)
    elev_ref = float(elev.min())

    cols, rows = np.meshgrid(np.arange(W), np.arange(H))
    top = _grid_to_model(cols.ravel(), rows.ravel(), elev.ravel(), elev.shape,
                         bbox, exaggeration, elev_ref, scale, width_m, height_m)
    n = H * W
    base_y = float(top[:, 1].min()) - base_units
    bottom = top.copy()
    bottom[:, 1] = base_y

    # Grid cell corners: a=top-left, b=top-right, d=bottom-left, e=bottom-right.
    r = np.arange(H - 1)[:, None]
    c = np.arange(W - 1)[None, :]
    a = (r * W + c).ravel()
    b, d, e = a + 1, a + W, a + W + 1

    top_faces = np.vstack([np.column_stack([a, d, b]),
                           np.column_stack([b, d, e])])
    bot_faces = np.vstack([np.column_stack([a, b, d]),
                           np.column_stack([b, e, d])]) + n

    def wall(seq: np.ndarray) -> np.ndarray:
        t0, t1 = seq[:-1], seq[1:]
        b0, b1 = t0 + n, t1 + n
        return np.vstack([np.column_stack([t0, t1, b1]),
                          np.column_stack([t0, b1, b0])])

    top_edge = np.arange(W)
    bottom_edge = (H - 1) * W + np.arange(W)
    left_edge = np.arange(H) * W
    right_edge = np.arange(H) * W + (W - 1)
    walls = np.vstack([wall(top_edge), wall(bottom_edge),
                       wall(left_edge), wall(right_edge)])

    positions = np.vstack([top, bottom]).astype(np.float32)

    # Orient each group so normals are consistent (top up, base down, walls out).
    centroid_xz = positions[:, [0, 2]].mean(axis=0)
    top_faces = _orient_vertical(positions, top_faces, want_up=True)
    bot_faces = _orient_vertical(positions, bot_faces, want_up=False)
    walls = _orient_outward(positions, walls, centroid_xz)
    faces = np.vstack([top_faces, bot_faces, walls]).astype(np.int32)

    span = float(elev.max() - elev.min()) or 1.0
    top_colors = _ramp_colors((elev.ravel() - elev_ref) / span)
    bottom_colors = np.tile(ROCK_COLOR, (n, 1))
    colors = np.vstack([top_colors, bottom_colors]).astype(np.uint8)

    # UVs: top from grid position; bottom copies its top vertex so walls drape.
    top_uv = np.column_stack([cols.ravel() / (W - 1), 1.0 - rows.ravel() / (H - 1)])
    uvs = np.vstack([top_uv, top_uv]).astype(np.float32)
    return positions, faces, colors, uvs


def build(elev: np.ndarray, bbox, *, mode: str = "adaptive",
          exaggeration: float = 1.5, detail: float = 0.5,
          source_meta: dict | None = None) -> BuiltMesh:
    """
    Build a coloured mesh from an elevation grid.

    mode        "adaptive" (TIN, surface) or "solid" (watertight, printable).
    detail      0..1 — higher keeps more triangles (smaller TIN max_error).
    """
    span = float(elev.max() - elev.min()) or 1.0

    if mode == "solid":
        positions, faces, colors, uvs = _build_solid(elev, bbox, exaggeration, base_units=10.0)
        method = "solid-grid"
    else:
        # Map detail → max_error in metres: more detail ⇒ tighter tolerance.
        max_error = max(span * (1.0 - detail) * 0.06, 0.05)
        positions, faces, colors, uvs = _build_tin(elev, bbox, exaggeration, max_error)
        method = "tin-rtin"

    mesh = trimesh.Trimesh(vertices=positions, faces=faces,
                           vertex_colors=colors, process=False)

    stats = {
        "vertices": int(len(positions)),
        "triangles": int(len(faces)),
        "min_elevation_m": round(float(elev.min()), 1),
        "max_elevation_m": round(float(elev.max()), 1),
        "relief_m": round(span, 1),
        "mode": mode,
        "method": method,
        "watertight": bool(mesh.is_watertight),
    }
    if source_meta:
        stats.update({k: source_meta[k] for k in ("source", "source_label") if k in source_meta})
    return BuiltMesh(mesh=mesh, positions=positions, faces=faces, colors=colors,
                     stats=stats, uvs=uvs)


def to_threejs(built: BuiltMesh) -> dict:
    """Flat arrays a three.js BufferGeometry can consume directly."""
    payload = {
        "positions": built.positions.reshape(-1).tolist(),
        "indices": built.faces.reshape(-1).tolist(),
        "colors": (built.colors[:, :3].astype(np.float32) / 255.0).reshape(-1).tolist(),
        "stats": built.stats,
    }
    if built.uvs is not None:
        payload["uvs"] = built.uvs.reshape(-1).tolist()
    return payload


def export_bytes(built: BuiltMesh, fmt: str) -> tuple[bytes, str]:
    """Serialise the mesh. Returns (data, mime_type)."""
    fmt = fmt.lower()
    if fmt == "gltf":
        fmt = "glb"
    mimes = {
        "obj": "text/plain",
        "stl": "model/stl",
        "glb": "model/gltf-binary",
    }
    if fmt not in mimes:
        raise ValueError(f"unsupported format: {fmt}")

    mesh = built.mesh
    # glTF can carry the draped map as an embedded texture; OBJ/STL stay geometry.
    if fmt == "glb" and built.texture_png is not None and built.uvs is not None:
        from PIL import Image  # local import keeps the module light
        image = Image.open(io.BytesIO(built.texture_png)).convert("RGB")
        mesh = built.mesh.copy()
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=built.uvs, image=image,
        )

    buf = io.BytesIO()
    mesh.export(buf, file_type=fmt)
    return buf.getvalue(), mimes[fmt]