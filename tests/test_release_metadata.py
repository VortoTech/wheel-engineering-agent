from fastapi.testclient import TestClient

from wheelcam.app import create_app


def test_v014_release_metadata_exposes_wheel_specific_capabilities(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    assert app.version == "0.15.0"
    health = TestClient(app).get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["template_version"] == "forged-monoblock-v15"
    assert body["capabilities"]["window_fit"] is True
    assert body["capabilities"]["window_method"] is True
    assert body["capabilities"]["independent_rim_pockets"] is True
    assert body["capabilities"]["window_side_draft"] is True
    assert body["capabilities"]["window_face_relief"] is True
    assert body["capabilities"]["window_spoke_ridge"] is True
