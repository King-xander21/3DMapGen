import pytest

import mesh
import terrain

BBOX = (-81.05, 46.45, -80.95, 46.52)


@pytest.fixture(scope="module")
def elev():
    grid, _ = terrain._synthetic(terrain.Bbox(*BBOX), 64)
    return grid


def test_solid_mode_is_watertight_for_3d_printing(elev):
    built = mesh.build(elev, BBOX, mode="solid")
    assert built.stats["watertight"] is True
    assert built.mesh.is_watertight


def test_lower_detail_produces_lighter_adaptive_mesh(elev):
    fine = mesh.build(elev, BBOX, mode="adaptive", detail=0.95)
    coarse = mesh.build(elev, BBOX, mode="adaptive", detail=0.1)
    assert coarse.stats["triangles"] < fine.stats["triangles"]


def test_threejs_payload_arrays_are_consistent(elev):
    built = mesh.build(elev, BBOX)
    payload = mesh.to_threejs(built)
    n = built.stats["vertices"]
    assert len(payload["positions"]) == n * 3
    assert len(payload["colors"]) == n * 3
    assert len(payload["indices"]) == built.stats["triangles"] * 3
    assert max(payload["indices"]) < n


@pytest.mark.parametrize("fmt", ["obj", "stl", "glb", "gltf"])
def test_exports_supported_formats(elev, fmt):
    data, mime = mesh.export_bytes(mesh.build(elev, BBOX), fmt)
    assert len(data) > 0
    assert mime


def test_rejects_unsupported_export_format(elev):
    with pytest.raises(ValueError):
        mesh.export_bytes(mesh.build(elev, BBOX), "fbx")
