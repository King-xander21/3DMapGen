import numpy as np
from PIL import Image

import terrain
from terrain import Bbox

SUDBURY = (-81.05, 46.45, -80.95, 46.52)


def test_decode_terrarium_matches_formula():
    # (R*256 + G + B/256) - 32768: R=128,G=0,B=0 is sea level; +1 in G is +1 m.
    pixels = np.array([[[128, 0, 0], [128, 100, 128]]], dtype=np.uint8)
    heights = terrain._decode_terrarium(Image.fromarray(pixels))
    assert heights[0, 0] == 0.0
    assert heights[0, 1] == 100.5


def test_bbox_clamped_orders_corners_and_limits_latitude():
    box = Bbox(min_lon=10, min_lat=89, max_lon=-10, max_lat=-89).clamped()
    assert (box.min_lon, box.max_lon) == (-10, 10)
    assert (box.min_lat, box.max_lat) == (-85.0, 85.0)


def test_falls_back_to_synthetic_when_offline():
    grid, meta = terrain.elevation_grid(SUDBURY, 64)
    assert meta["source"] == "synthetic"
    assert "fallback_reason" in meta
    assert grid.shape == (64, 64)
    assert np.isfinite(grid).all()


def test_synthetic_terrain_is_reproducible_for_same_place():
    a, _ = terrain.elevation_grid(SUDBURY, 48)
    b, _ = terrain.elevation_grid(SUDBURY, 48)
    np.testing.assert_array_equal(a, b)


def test_resolution_is_clamped_to_safe_range():
    small, _ = terrain.elevation_grid(SUDBURY, 1)
    large, _ = terrain.elevation_grid(SUDBURY, 10_000)
    assert small.shape == (16, 16)
    assert large.shape == (320, 320)
