# 3DMapGen
3D map generator

# maps3d demo

A small, self-contained demonstration of the pipeline behind a tool like
[maps3d.io](https://maps3d.io): pick a patch of the earth, pull elevation data
for it, and turn that into a downloadable, colour-tinted 3D model.

The point of the demo is to make the *pipeline* legible — data in, mesh out —
rather than to compete on data coverage or polish.

## What it does

You select a location and an area on the map, choose a few parameters, and the
backend fuses open elevation data into a textured 3D mesh that renders live in
the browser and exports to `.glTF`, `.obj`, and `.stl`.

Two meshing strategies illustrate the central tradeoff:

- **Adaptive (TIN)** builds a feature-adaptive Triangulated Irregular Network —
  flat ground spends few triangles, rugged ground spends many, so the surface
  stays light. It uses a pure-NumPy port of the RTIN algorithm (`rtin.py`, the
  same right-triangulated approach as Mapbox's `martini`), so there's nothing to
  compile — it runs on any Python with no C++ toolchain.
- **Solid** closes that surface with a flat base and four walls to produce a
  *watertight* mesh, which is what a 3D-printer slicer needs.

## Where the data comes from

Elevation is pulled from **Terrarium terrain tiles** on the AWS Open Data
registry — the open Mapzen/Nextzen dataset, no API key required. Height is
packed into each tile's RGB channels and decoded as
`(R·256 + G + B/256) − 32768` metres.

If no elevation service is reachable (offline, or the request fails), the app
falls back to **deterministic synthetic terrain** so it always produces a model
to look at. A badge in the header tells you which source you're seeing.

Satellite-imagery draping is deliberately left out; the mesh is instead tinted
with a hypsometric (elevation) colour ramp, which keeps the demo free of any
imagery-licensing question. That ramp is also the app's visual signature — it
appears as the legend beside the viewer.

## Architecture

```
terrain.py   bounding box  → elevation grid   (real DEM tiles, else synthetic)
mesh.py      elevation grid → coloured mesh    (TIN or watertight solid) + export
app.py       Flask: serves the editor, POST /api/generate, GET /api/download
templates/   the editor UI
static/      three.js viewer + Leaflet picker + styles
```

The browser talks to two endpoints: `POST /api/generate` returns flat geometry
arrays for a three.js `BufferGeometry` plus mesh statistics; each generated mesh
is cached briefly in memory so `GET /api/download/<id>.<fmt>` can re-serialise
it to any format on demand.

## Run it

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

`requirements.txt` installs cleanly on any Python with no build tools — every
dependency is pure Python or ships prebuilt wheels, and the adaptive TIN is
implemented in NumPy rather than a compiled extension.

Real elevation data needs outbound access to `s3.amazonaws.com`; without it
you'll get the synthetic fallback, which is still fully functional end to end.

## Where you'd take it next

The honest gaps between this and a production tool are: draping real
(open-licensed) orthoimagery for photographic texture; extruding OpenStreetMap
or Overture building footprints onto the terrain; correct georeferenced `.dxf`
and `.ifc` export for CAD/BIM; and tiling so large areas don't blow the tile
budget. None of those change the shape of the pipeline above — they extend it.
