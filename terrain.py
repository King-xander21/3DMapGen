"""
terrain.py — turn a bounding box into a grid of elevations.

Two paths, tried in order:

1. Real open elevation data. We pull Terrarium-encoded terrain tiles from the
   AWS Open Data mirror of the Mapzen/Nextzen dataset. No API key, no account,
   commercial-friendly — exactly the kind of open source that lets the output
   be reused freely. Elevation is packed into the RGB channels of a PNG:

       height_metres = (R * 256 + G + B / 256) - 32768

2. Synthetic fallback. When there's no network (or the fetch fails), we
   generate deterministic fractal terrain so the app still produces something
   to look at. Same bounding box always yields the same hills.

The public entry point is `elevation_grid(bbox, resolution)`, which returns a
square float32 array of metres plus a small metadata dict describing where the
numbers came from.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
import requests
from PIL import Image

# AWS Open Data mirror of the Nextzen/Mapzen terrain tiles. Open licence.
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
# Standard OpenStreetMap raster (roads, water, land use, labels) for the map drape.
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_SIZE = 256
TILE_BUDGET = 12          # most tiles we'll stitch for one request
REQUEST_TIMEOUT = 8       # seconds per tile
HTTP_HEADERS = {"User-Agent": "maps3d-demo/1.0 (terrain fetch)"}


@dataclass
class Bbox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def mid_lat(self) -> float:
        return (self.min_lat + self.max_lat) / 2.0

    def clamped(self) -> "Bbox":
        """Keep inside Web Mercator's valid latitude band and a sane order."""
        lo_lat = max(min(self.min_lat, self.max_lat), -85.0)
        hi_lat = min(max(self.min_lat, self.max_lat), 85.0)
        lo_lon = min(self.min_lon, self.max_lon)
        hi_lon = max(self.min_lon, self.max_lon)
        return Bbox(lo_lon, lo_lat, hi_lon, hi_lat)


def _deg2num(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Fractional slippy-map tile coordinates for a lat/lon at zoom z."""
    lat_r = math.radians(lat)
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _pick_zoom(bbox: Bbox) -> tuple[int, int, int, int, int]:
    """
    Choose the highest zoom whose tile span fits the budget, so we get the most
    detail we can afford. Returns (zoom, tile_x0, tile_y0, n_tiles_x, n_tiles_y),
    where the origin tile is the top-left (min_lon, max_lat) corner.
    """
    for z in range(14, 1, -1):
        x0f, y0f = _deg2num(bbox.max_lat, bbox.min_lon, z)  # top-left
        x1f, y1f = _deg2num(bbox.min_lat, bbox.max_lon, z)  # bottom-right
        tx0, ty0 = int(math.floor(x0f)), int(math.floor(y0f))
        nx = int(math.floor(x1f)) - tx0 + 1
        ny = int(math.floor(y1f)) - ty0 + 1
        if nx * ny <= TILE_BUDGET:
            return z, tx0, ty0, nx, ny
    # Fallback to a single low-zoom tile.
    z = 2
    x0f, y0f = _deg2num(bbox.max_lat, bbox.min_lon, z)
    return z, int(x0f), int(y0f), 1, 1


def _decode_terrarium(img: Image.Image) -> np.ndarray:
    rgb = np.asarray(img.convert("RGB")).astype(np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (r * 256.0 + g + b / 256.0) - 32768.0


def _fetch_real(bbox: Bbox, resolution: int) -> tuple[np.ndarray, dict]:
    z, tx0, ty0, nx, ny = _pick_zoom(bbox)

    mosaic = np.zeros((ny * TILE_SIZE, nx * TILE_SIZE), dtype=np.float32)
    for j in range(ny):
        for i in range(nx):
            url = TILE_URL.format(z=z, x=tx0 + i, y=ty0 + j)
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HTTP_HEADERS)
            resp.raise_for_status()
            tile = Image.open(io.BytesIO(resp.content))
            block = _decode_terrarium(tile)
            mosaic[j * TILE_SIZE:(j + 1) * TILE_SIZE,
                   i * TILE_SIZE:(i + 1) * TILE_SIZE] = block

    # Where the bbox corners land inside the stitched mosaic, in pixels.
    x0f, y0f = _deg2num(bbox.max_lat, bbox.min_lon, z)
    x1f, y1f = _deg2num(bbox.min_lat, bbox.max_lon, z)
    left = (x0f - tx0) * TILE_SIZE
    right = (x1f - tx0) * TILE_SIZE
    top = (y0f - ty0) * TILE_SIZE
    bottom = (y1f - ty0) * TILE_SIZE

    l, r = int(math.floor(left)), int(math.ceil(right))
    t, b = int(math.floor(top)), int(math.ceil(bottom))
    l, t = max(l, 0), max(t, 0)
    r, b = min(r, mosaic.shape[1]), min(b, mosaic.shape[0])
    window = mosaic[t:b, l:r]
    if window.size == 0:
        raise ValueError("empty crop window")

    # Resample the (float) window to a clean square grid.
    grid = np.asarray(
        Image.fromarray(window, mode="F").resize((resolution, resolution), Image.BILINEAR),
        dtype=np.float32,
    )
    meta = {
        "source": "real",
        "source_label": "Terrarium DEM · AWS Open Data",
        "zoom": z,
        "tiles": nx * ny,
    }
    return grid, meta


def _value_noise(resolution: int, octaves: int, rng: np.random.Generator) -> np.ndarray:
    """Fractal value noise: sum of upsampled random grids at halving amplitude."""
    out = np.zeros((resolution, resolution), dtype=np.float64)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = 2 ** (o + 1) + 1
        coarse = rng.random((cells, cells))
        layer = np.asarray(
            Image.fromarray((coarse * 255).astype(np.uint8)).resize(
                (resolution, resolution), Image.BILINEAR
            ),
            dtype=np.float64,
        ) / 255.0
        out += amp * layer
        total += amp
        amp *= 0.5
    return out / total


def _synthetic(bbox: Bbox, resolution: int) -> tuple[np.ndarray, dict]:
    # Seed from the bbox so a given place is reproducible run to run.
    seed = int(abs(bbox.min_lon * 1000) * 100000 + abs(bbox.min_lat * 1000)) % (2 ** 32)
    rng = np.random.default_rng(seed)

    base = _value_noise(resolution, octaves=6, rng=rng)
    # Sharpen valleys, then lift a broad ridge so it reads as mountainous.
    relief = np.power(base, 1.6)
    yy, xx = np.mgrid[0:resolution, 0:resolution] / (resolution - 1)
    ridge = np.exp(-((xx - 0.5) ** 2) / 0.12) * (0.4 + 0.5 * rng.random())
    field = 0.75 * relief + 0.25 * ridge
    field = (field - field.min()) / (field.max() - field.min() + 1e-9)

    grid = (field * 1400.0).astype(np.float32)  # ~0–1400 m of relief
    meta = {"source": "synthetic", "source_label": "Synthetic terrain (offline)"}
    return grid, meta


def elevation_grid(bbox_tuple: tuple[float, float, float, float],
                   resolution: int) -> tuple[np.ndarray, dict]:
    """
    Return (elevation[resolution, resolution] float32 metres, meta dict).

    Tries real open data first; on any failure returns synthetic terrain so the
    caller always has a heightfield to mesh.
    """
    bbox = Bbox(*bbox_tuple).clamped()
    resolution = int(max(16, min(resolution, 320)))
    try:
        return _fetch_real(bbox, resolution)
    except Exception as exc:  # noqa: BLE001 — any failure falls back gracefully
        grid, meta = _synthetic(bbox, resolution)
        meta["fallback_reason"] = str(exc)[:200]
        return grid, meta

def map_raster(bbox_tuple: tuple[float, float, float, float],
               target_px: int = 1024, tile_budget: int = 24) -> Image.Image:
    """
    Fetch and stitch OpenStreetMap raster tiles for `bbox` into a single RGB
    image, cropped to the box and resized to `target_px` square. This is the
    road/water/land-use map that gets draped over the terrain as a texture.

    Raises on any network failure so the caller can fall back to elevation tint.
    Attribution: © OpenStreetMap contributors.
    """
    bbox = Bbox(*bbox_tuple).clamped()

    # Highest zoom whose tile span fits the budget → sharpest map we can afford.
    chosen = None
    for z in range(18, 1, -1):
        x0f, y0f = _deg2num(bbox.max_lat, bbox.min_lon, z)  # top-left
        x1f, y1f = _deg2num(bbox.min_lat, bbox.max_lon, z)  # bottom-right
        nx = int(math.floor(x1f)) - int(math.floor(x0f)) + 1
        ny = int(math.floor(y1f)) - int(math.floor(y0f)) + 1
        if nx * ny <= tile_budget:
            chosen = (z, x0f, y0f, x1f, y1f, nx, ny)
            break
    if chosen is None:
        z = 2
        x0f, y0f = _deg2num(bbox.max_lat, bbox.min_lon, z)
        x1f, y1f = _deg2num(bbox.min_lat, bbox.max_lon, z)
        chosen = (z, x0f, y0f, x1f, y1f, 1, 1)

    z, x0f, y0f, x1f, y1f, nx, ny = chosen
    tx0, ty0 = int(math.floor(x0f)), int(math.floor(y0f))

    mosaic = Image.new("RGB", (nx * TILE_SIZE, ny * TILE_SIZE))
    for j in range(ny):
        for i in range(nx):
            url = OSM_TILE_URL.format(z=z, x=tx0 + i, y=ty0 + j)
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HTTP_HEADERS)
            resp.raise_for_status()
            tile = Image.open(io.BytesIO(resp.content)).convert("RGB")
            mosaic.paste(tile, (i * TILE_SIZE, j * TILE_SIZE))

    left = (x0f - tx0) * TILE_SIZE
    right = (x1f - tx0) * TILE_SIZE
    top = (y0f - ty0) * TILE_SIZE
    bottom = (y1f - ty0) * TILE_SIZE
    crop = mosaic.crop((int(left), int(top), math.ceil(right), math.ceil(bottom)))
    return crop.resize((target_px, target_px), Image.LANCZOS)