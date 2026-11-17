BBOX = [-81.05, 46.45, -80.95, 46.52]


def generate(client, **params):
    return client.post("/api/generate", json={"bbox": BBOX, "resolution": 64, **params})


def test_generate_returns_geometry_and_id(client):
    resp = generate(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"]
    assert body["positions"] and body["indices"]
    assert body["stats"]["source"] == "synthetic"


def test_generate_rejects_missing_or_malformed_bbox(client):
    assert client.post("/api/generate", json={}).status_code == 400
    assert client.post("/api/generate", json={"bbox": [1, 2, 3]}).status_code == 400


def test_map_surface_falls_back_to_elevation_tint_when_tiles_fail(client):
    stats = generate(client, surface="map").get_json()["stats"]
    assert stats["has_texture"] is False
    assert "texture_note" in stats


def test_download_round_trip(client):
    mid = generate(client, mode="solid").get_json()["id"]
    resp = client.get(f"/api/download/{mid}.stl")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    assert len(resp.data) > 0


def test_download_unknown_model_is_404(client):
    assert client.get("/api/download/does-not-exist.obj").status_code == 404


def test_download_bad_format_is_400(client):
    mid = generate(client).get_json()["id"]
    assert client.get(f"/api/download/{mid}.fbx").status_code == 400
