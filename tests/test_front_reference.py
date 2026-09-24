import io
import json

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from wheelcam.app import create_app
from wheelcam.front_reference import estimate_tilt_deg, rectify_front, symmetry_guide


def synthetic_ellipse():
    image = Image.new("RGB", (240, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 30, 200, 150), fill="#30343a", outline="black", width=3)
    draw.ellipse((116, 86, 124, 94), fill="red")
    return image


def test_rectification_maps_ellipse_to_circle_and_masks_background():
    result = rectify_front(
        synthetic_ellipse(),
        {"cx": 120, "cy": 90, "rx": 80, "ry": 60},
        output_size=200,
    )
    assert result.size == (200, 200)
    alpha = np.asarray(result.getchannel("A"))
    assert alpha[0, 0] == 0 and alpha[100, 100] == 255
    # The dark ellipse reaches the same radius horizontally and vertically.
    rgb = np.asarray(result.convert("RGB"))
    assert rgb[100, 10].mean() < 100
    assert rgb[10, 100].mean() < 100
    assert tuple(rgb[100, 100]) == (255, 0, 0)
    assert round(estimate_tilt_deg({"rx": 80, "ry": 60}), 2) == 41.41


def test_symmetry_guide_is_deterministic_and_preserves_mask():
    front = rectify_front(synthetic_ellipse(), {"cx": 120, "cy": 90, "rx": 80, "ry": 60}, output_size=200)
    first = symmetry_guide(front, 5)
    second = symmetry_guide(front, 5)
    assert first.tobytes() == second.tobytes()
    assert first.getchannel("A").tobytes() == front.getchannel("A").tobytes()


def test_front_reference_endpoint_uses_current_image_analysis(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        project = client.post("/api/projects", json={"name": "正视校正"}).json()
        data = io.BytesIO()
        synthetic_ellipse().save(data, "PNG")
        project = client.post(
            f"/api/projects/{project['id']}/images",
            files={"file": ("wheel.png", data.getvalue(), "image/png")},
        ).json()
        image = project["images"][0]
        analysis = {
            "id": "ellipse-fit", "image_id": image["id"], "image_sha256": image["sha256"],
            "image_size": [240, 180], "ellipse": {"cx": 120, "cy": 90, "rx": 80, "ry": 60},
            "spokes": {"groups": 5}, "created_at": "2026-09-20T00:00:00Z",
        }
        with client.app.state.store.connection() as db:
            db.execute(
                "INSERT INTO image_analyses VALUES(?,?,?,?,?)",
                (analysis["id"], project["id"], image["id"], json.dumps(analysis), analysis["created_at"]),
            )
        response = client.get(f"/api/projects/{project['id']}/front-reference")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "ellipse-to-circle-v1" in response.headers["x-wheelcam-derivation"]
        assert Image.open(io.BytesIO(response.content)).size == (240, 240)
        assert client.get(f"/api/projects/{project['id']}/front-reference?mode=symmetry").status_code == 200
        assert client.get(f"/api/projects/{project['id']}/front-reference?mode=bad").status_code == 422
