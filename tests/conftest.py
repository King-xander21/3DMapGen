import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import terrain  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Never hit the network in tests: real elevation and map tiles always 'fail',
    which exercises the synthetic-terrain fallback path."""
    def no_network(*args, **kwargs):
        raise ConnectionError("network disabled in tests")

    monkeypatch.setattr(terrain, "_fetch_real", no_network)
    monkeypatch.setattr(terrain, "map_raster", no_network)


@pytest.fixture
def bumpy():
    """Deterministic rough 33x33 heightfield (grid_size - 1 is a power of two)."""
    rng = np.random.default_rng(42)
    return rng.random((33, 33)).astype(np.float32) * 100.0


@pytest.fixture
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()
