"""Window-fit endpoint: a label saved by the annotation tool for the uploaded file becomes a reviewable
candidate, and applying it switches the draft to the window method."""
import hashlib
import io
import json
import math

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_window_fit import GROUPS, swept_window, synthetic
from wheelcam.app import create_app
from wheelcam.models import WheelSpec
from wheelcam.photo_pose import rotation
from wheelcam.template import FLANGE_HEIGHT, INCH, layout


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        client.data_dir = tmp_path
        yield client


def upload(client, project):
    buffer = io.BytesIO()
    Image.new("RGB", (1400, 1300), (200, 200, 200)).save(buffer, "PNG")
    content = buffer.getvalue()
    response = client.post(f"/api/projects/{project['id']}/images", files={"file": ("wheel.png", content, "image/png")})
    assert response.status_code == 201
    return response.json(), hashlib.sha256(content).hexdigest()


def test_window_fit_candidate_applies_to_draft(client):
    project = client.post("/api/projects", json={"name": "窗口法"}).json()
    project, sha = upload(client, project)
    image_id = project["primary_image_id"]
    url = f"/api/projects/{project['id']}/images/{image_id}/window-fit"
    missing = client.post(url, json={"expected_revision": project["revision"]})
    assert missing.status_code == 404 and "窗口标注" in missing.json()["detail"]

    spec = WheelSpec(**{**project["spec"], "spoke_count": GROUPS})
    outer = spec.rim_diameter_in * INCH / 2 + FLANGE_HEIGHT
    pose = {"cx": 700.0, "cy": 650.0, "scale_px": 560.0, "distance_radii": 5.0,
            "rotation": rotation(0.12, 0.04, 0.0).tolist(), "radius_mm": outer,
            "reference_z_mm": spec.rim_width_in * INCH / 2 + layout(spec)["derived"]["flange_thickness_mm"],
            "angles_deg": [math.degrees(0.12), math.degrees(0.04), 0.0]}
    label = synthetic(spec, swept_window(), pose)
    label["image"]["sha256"] = sha
    (client.data_dir / "annotations").mkdir()
    (client.data_dir / "annotations" / f"{sha}.json").write_text(json.dumps(label))

    result = client.post(url, json={"expected_revision": project["revision"]})
    assert result.status_code == 200, result.text
    candidate = result.json()
    assert candidate["can_apply"] and candidate["window_fit"]["held_out_iou_mean"] > 0.9
    assert candidate["suggested_parameters"]["spoke_method"] == "window"
    assert max(candidate["image_size"]) <= 1600 and candidate["camera_fit"]["pose"]["scale_px"] > 0
    assert client.get(f"/api/projects/{project['id']}").json()["photo_analysis"]["id"] == candidate["id"]

    applied = client.post(f"/api/projects/{project['id']}/analyses/{candidate['id']}/apply",
                          json={"expected_revision": project["revision"]})
    assert applied.status_code == 200, applied.text
    spec = applied.json()["spec"]
    assert spec["spoke_method"] == "window" and spec["spoke_count"] == GROUPS and len(spec["window_outlines_mm"]) == 1
    assert applied.json()["sources"]["window_outlines_mm"]["kind"] == "manual"

    stale = client.post(url, json={"expected_revision": project["revision"]})
    assert stale.status_code == 409
