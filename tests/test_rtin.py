import numpy as np
import pytest

from rtin import Martini, get_martini


def test_rejects_grid_size_that_is_not_power_of_two_plus_one():
    with pytest.raises(ValueError):
        Martini(100)


def test_flat_terrain_collapses_to_two_triangles():
    flat = np.zeros((33, 33), dtype=np.float32)
    vertices, triangles = Martini(33).get_mesh(flat, max_error=0.0)
    assert len(triangles) == 2
    assert len(vertices) == 4


def test_zero_tolerance_keeps_full_resolution(bumpy):
    size = 32
    _, triangles = Martini(33).get_mesh(bumpy, max_error=0.0)
    assert len(triangles) == size * size * 2


def test_looser_tolerance_never_adds_triangles(bumpy):
    m = Martini(33)
    counts = [len(m.get_mesh(bumpy, max_error=e)[1]) for e in (0.0, 5.0, 20.0, 60.0)]
    assert counts == sorted(counts, reverse=True)
    assert counts[-1] < counts[0]


def test_mesh_indices_and_coordinates_are_valid(bumpy):
    vertices, triangles = Martini(33).get_mesh(bumpy, max_error=10.0)
    assert vertices.min() >= 0 and vertices.max() <= 32
    assert triangles.min() >= 0 and triangles.max() < len(vertices)
    # No degenerate triangles (all three corners distinct).
    assert all(len(set(t)) == 3 for t in triangles.tolist())


def test_setup_is_cached_per_grid_size():
    assert get_martini(17) is get_martini(17)
