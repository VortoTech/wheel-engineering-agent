from fastapi.testclient import TestClient

from wheelcam.app import create_app


def test_v010_release_metadata_exposes_window_capabilities(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    assert app.version == "0.10.0"
    health = TestClient(app).get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["template_version"] == "forged-monoblock-v10"
    assert body["capabilities"]["window_fit"] is True
    assert body["capabilities"]["window_method"] is True
