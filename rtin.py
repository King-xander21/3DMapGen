"""
rtin.py — adaptive terrain triangulation in pure Python, no native build.

This is a NumPy port of the RTIN algorithm (Evans, Kirkpatrick & Townsend),
the same right-triangulated approach used by Mapbox's `martini`. It replaces
`pydelatin`, which needs a C++ toolchain and the `glm` headers to compile —
awkward on fresh Python versions with no prebuilt wheel.

RTIN works on a square grid whose side is a power of two plus one (65, 129,
257, …). It builds a hierarchy of right-isosceles triangles, records the
approximation error introduced at each possible split point, then emits only
the triangles needed to keep every point within `max_error` of the true
surface. Flat ground collapses to a few big triangles; rugged ground keeps the
detail — and the result is crack-free by construction.
"""

from __future__ import annotations

import numpy as np

_CACHE: dict[int, "Martini"] = {}


def get_martini(grid_size: int) -> "Martini":
    """Cache the per-grid-size setup, which is independent of the actual heights."""
    m = _CACHE.get(grid_size)
    if m is None:
        m = Martini(grid_size)
        _CACHE[grid_size] = m
    return m


class Martini:
    def __init__(self, grid_size: int = 257):
        size = grid_size - 1
        if size & (size - 1):
            raise ValueError("grid_size - 1 must be a power of two (e.g. 65, 129, 257)")
        self.grid_size = grid_size
        self.num_triangles = size * size * 2 - 2
        self.num_parent_triangles = self.num_triangles - size * size

        # Hypotenuse endpoints (ax, ay, bx, by) for every triangle in the hierarchy.
        coords = np.zeros(self.num_triangles * 4, dtype=np.int32)
        for i in range(self.num_triangles):
            id_ = i + 2
            ax = ay = bx = by = cx = cy = 0
            if id_ & 1:
                bx = by = cx = size       # bottom triangle of the split
            else:
                ax = ay = cy = size       # top triangle of the split
            id_ >>= 1
            while id_ > 1:
                mx = (ax + bx) >> 1
                my = (ay + by) >> 1
                if id_ & 1:               # left child
                    bx, by = ax, ay
                    ax, ay = cx, cy
                else:                     # right child
                    ax, ay = bx, by
                    bx, by = cx, cy
                cx, cy = mx, my
                id_ >>= 1
            k = i * 4
            coords[k], coords[k + 1], coords[k + 2], coords[k + 3] = ax, ay, bx, by
        self.coords = coords

    def build_errors(self, terrain: np.ndarray) -> np.ndarray:
        """
        Bottom-up pass: max approximation error carried by each grid vertex.
        `terrain` is a (grid_size, grid_size) float array.
        """
        gs = self.grid_size
        heights = terrain.astype(np.float64).reshape(-1)
        errors = np.zeros(gs * gs, dtype=np.float64)
        coords = self.coords

        for i in range(self.num_triangles - 1, -1, -1):
            k = i * 4
            ax, ay, bx, by = coords[k], coords[k + 1], coords[k + 2], coords[k + 3]
            mx, my = (ax + bx) >> 1, (ay + by) >> 1
            cx, cy = mx + my - ay, my + ax - mx

            interpolated = (heights[ay * gs + ax] + heights[by * gs + bx]) / 2.0
            middle = my * gs + mx
            err = abs(interpolated - heights[middle])
            if err > errors[middle]:
                errors[middle] = err

            if i < self.num_parent_triangles:
                left = ((ay + cy) >> 1) * gs + ((ax + cx) >> 1)
                right = ((by + cy) >> 1) * gs + ((bx + cx) >> 1)
                child = max(errors[left], errors[right])
                if child > errors[middle]:
                    errors[middle] = child
        return errors

    def get_mesh(self, terrain: np.ndarray, max_error: float = 0.0):
        """
        Top-down pass: emit (vertices Nx2 int, triangles Mx3 int) honouring
        `max_error`. Vertices are (x, y) grid coordinates; sample heights from
        `terrain[y, x]` afterwards.
        """
        gs = self.grid_size
        size = gs - 1
        errors = self.build_errors(terrain)
        indices = np.zeros(gs * gs, dtype=np.int64)

        counters = {"v": 0, "t": 0}

        def count(ax, ay, bx, by, cx, cy):
            mx, my = (ax + bx) >> 1, (ay + by) >> 1
            if abs(ax - cx) + abs(ay - cy) > 1 and errors[my * gs + mx] > max_error:
                count(cx, cy, ax, ay, mx, my)
                count(bx, by, cx, cy, mx, my)
            else:
                for idx in (ay * gs + ax, by * gs + bx, cy * gs + cx):
                    if indices[idx] == 0:
                        counters["v"] += 1
                        indices[idx] = counters["v"]
                counters["t"] += 1

        count(0, 0, size, size, size, 0)
        count(size, size, 0, 0, 0, size)

        vertices = np.zeros((counters["v"], 2), dtype=np.int32)
        triangles = np.zeros((counters["t"], 3), dtype=np.int32)
        tri = {"i": 0}

        def emit(ax, ay, bx, by, cx, cy):
            mx, my = (ax + bx) >> 1, (ay + by) >> 1
            if abs(ax - cx) + abs(ay - cy) > 1 and errors[my * gs + mx] > max_error:
                emit(cx, cy, ax, ay, mx, my)
                emit(bx, by, cx, cy, mx, my)
            else:
                a = indices[ay * gs + ax] - 1
                b = indices[by * gs + bx] - 1
                c = indices[cy * gs + cx] - 1
                vertices[a] = (ax, ay)
                vertices[b] = (bx, by)
                vertices[c] = (cx, cy)
                triangles[tri["i"]] = (a, b, c)
                tri["i"] += 1

        emit(0, 0, size, size, size, 0)
        emit(size, size, 0, 0, 0, size)
        return vertices, triangles
