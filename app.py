"""
app.py — Flask backend for the maps3d demo.

Endpoints
    GET  /                         the editor UI
    POST /api/generate             bbox + params  →  three.js geometry + stats
    GET  /api/download/<id>.<fmt>  export a previously generated mesh (obj/stl/glb)

Generated meshes are held briefly in memory, keyed by id, so the download
routes can re-serialise them in whatever format the user asks for without
rebuilding.
"""

from __future__ import annotations

import os
import uuid
from collections import OrderedDict

from flask import Flask, abort, jsonify, render_template, request, send_file
import io
import requests

import mesh as meshlib
import terrain

# Find the templates/ and static/ files whether they sit in subfolders next to
# this file (the intended layout) or flat in the same directory (a common result
# of copying files across by hand). This keeps the app running either way.
_BASE = os.path.dirname(os.path.abspath(__file__))
_TPL = os.path.join(_BASE, "templates")
_STATIC = os.path.join(_BASE, "static")
app = Flask(
    __name__,
    template_folder=_TPL if os.path.isdir(_TPL) else _BASE,
    static_folder=_STATIC if os.path.isdir(_STATIC) else _BASE,
    static_url_path="/static",
)

_CACHE: "OrderedDict[str, meshlib.BuiltMesh]" = OrderedDict()
_CACHE_LIMIT = 24


def _remember(built: meshlib.BuiltMesh) -> str:
    mid = uuid.uuid4().hex[:12]
    _CACHE[mid] = built
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)
    return mid


def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(float(value), hi))
    except (TypeError, ValueError):
        return default


@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception:
        return (
            "<h1>index.html not found</h1><p>Expected it in a <code>templates</code> "
            "folder next to app.py, or flat in the same folder. Current app dir: "
            f"<code>{_BASE}</code></p>",
            500,
        )


@app.get("/api/search")
def search():
    """Geocode a place name via OpenStreetMap Nominatim (free, no key)."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 0},
            headers={"User-Agent": "maps3d-demo/1.0 (terrain demo)"},
            timeout=8,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:  # noqa: BLE001 — offline / rate-limited / bad response
        return jsonify({"error": f"search unavailable: {exc}"}), 502

    results = []
    for r in rows:
        try:
            # Nominatim boundingbox is [south, north, west, east] as strings.
            bb = r.get("boundingbox")
            bbox = [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])] if bb else None
            results.append({
                "name": r.get("display_name", query),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "bbox": bbox,  # [min_lon, min_lat, max_lon, max_lat]
            })
        except (KeyError, ValueError, TypeError):
            continue
    return jsonify(results)


@app.post("/api/generate")
def generate():
    data = request.get_json(silent=True) or {}
    bbox = data.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        abort(400, "bbox must be [min_lon, min_lat, max_lon, max_lat]")

    resolution = int(_clamp(data.get("resolution"), 32, 260, 140))
    exaggeration = _clamp(data.get("exaggeration"), 0.2, 5.0, 1.5)
    detail = _clamp(data.get("detail"), 0.0, 1.0, 0.55)
    mode = "solid" if data.get("mode") == "solid" else "adaptive"
    surface = "map" if data.get("surface") == "map" else "elevation"

    elev, source_meta = terrain.elevation_grid(tuple(bbox), resolution)
    built = meshlib.build(
        elev, tuple(bbox), mode=mode, exaggeration=exaggeration,
        detail=detail, source_meta=source_meta,
    )

    # Optional road-map drape: fetch OSM raster and attach it as a texture.
    if surface == "map":
        try:
            image = terrain.map_raster(tuple(bbox))
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            built.texture_png = buf.getvalue()
            built.stats["has_texture"] = True
            built.stats["attribution"] = "© OpenStreetMap contributors"
        except Exception as exc:  # noqa: BLE001 — tiles offline/blocked → fall back
            built.stats["has_texture"] = False
            built.stats["texture_note"] = f"Map tiles unavailable ({str(exc)[:80]}) — showing elevation tint."
    else:
        built.stats["has_texture"] = False

    mid = _remember(built)
    payload = meshlib.to_threejs(built)
    payload["id"] = mid
    if source_meta.get("source") == "synthetic":
        payload["stats"]["note"] = "No elevation service reachable — showing synthetic terrain."
    return jsonify(payload)


@app.get("/api/texture/<mid>.png")
def texture(mid: str):
    built = _CACHE.get(mid)
    if built is None or built.texture_png is None:
        abort(404, "texture not found — generate a map-surface model first")
    return send_file(io.BytesIO(built.texture_png), mimetype="image/png")


@app.get("/api/download/<mid>.<fmt>")
def download(mid: str, fmt: str):
    built = _CACHE.get(mid)
    if built is None:
        abort(404, "model expired — generate it again")
    try:
        data, mime = meshlib.export_bytes(built, fmt)
    except ValueError as exc:
        abort(400, str(exc))
    ext = "glb" if fmt.lower() == "gltf" else fmt.lower()
    return send_file(
        io.BytesIO(data),
        mimetype=mime,
        as_attachment=True,
        download_name=f"terrain-{mid}.{ext}",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)