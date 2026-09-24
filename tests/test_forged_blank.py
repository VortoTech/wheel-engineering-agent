import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wheelcam.app import create_app
from wheelcam.forged_blank import TEMPLATE_VERSION, recipe_from_dict

# Plain spokes, no facets / edge break / grooves / pockets: the real CAD path in seconds.
FAST = {"family": "single", "spokes": 5, "facet_deg": 0, "edge_break": 0, "groove_offsets": [],
        "back_pocket_skin": 0, "lip_pockets": 0}


def test_recipe_validation():
    assert recipe_from_dict({"_photo": "x.jpg"}).spokes == 6   # render hints are dropped
    with pytest.raises(ValueError, match="未知字段"):
        recipe_from_dict({"spokez": 5})
    with pytest.raises(ValueError, match="轮辐结构"):
        recipe_from_dict({"family": "mesh"})
    with pytest.raises(ValueError, match="nodes"):
        recipe_from_dict({"family": "skeleton"})
    with pytest.raises(ValueError, match="3–12"):
        recipe_from_dict({"spokes": 2})


def test_build_subprocess_dispatches_forged_template(tmp_path):
    snapshot = {"template": TEMPLATE_VERSION, "forged": FAST, "model_id": "m1", "draft_revision": 3}
    (tmp_path / "recipe.json").write_text(json.dumps(snapshot))
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "wheelcam.build", str(tmp_path / "recipe.json"), str(tmp_path)],
                   check=True, cwd=root, env={"PYTHONPATH": str(root / "services")}, timeout=600)
    report = json.loads((tmp_path / "report.json").read_text())
    # Fields the web viewer requires on every report.
    for key in ("checks", "solid_count", "volume_mm3", "bbox_mm", "face_count", "template_version", "limitations", "artifacts"):
        assert key in report
    assert report["template_version"] == TEMPLATE_VERSION
    assert all(report["checks"].values()) and report["solid_count"] == 1
    assert {"wheel.step", "wheel.glb", "stock.step", "recipe.json"} <= set(report["artifacts"])
    assert report["manufacturing_status"] == "not_released" and report["model_id"] == "m1"
    assert 0 < report["forged"]["removal_ratio"] < 1
    # Main-template coordinates: Z=0 at the rim-width mid-plane, so the part is centred in Z.
    import cadquery as cq
    bbox = cq.importers.importStep(str(tmp_path / "wheel.step")).val().BoundingBox()
    assert abs(bbox.zmin + bbox.zmax) < 1e-3
    assert abs(bbox.zlen - recipe_from_dict(FAST).width) < 1e-3


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        yield client


def test_forged_build_endpoint_queues_template_snapshot(client):
    project = client.post("/api/projects", json={"name": "锻坯"}).json()
    url = f'/api/projects/{project["id"]}/forged-builds'
    assert client.post(url, json={"expected_revision": 1, "recipe": {"spokez": 5}}).status_code == 422
    queued = client.post(url, json={"expected_revision": 1, "recipe": FAST})
    assert queued.status_code == 202 and queued.json()["template"] == TEMPLATE_VERSION
    job = next(j for j in client.get(f'/api/projects/{project["id"]}').json()["jobs"] if j["id"] == queued.json()["id"])
    snapshot = job["snapshot"]
    assert snapshot["template"] == snapshot["template_version"] == TEMPLATE_VERSION
    assert snapshot["forged"]["spokes"] == 5 and snapshot["forged"]["family"] == "single"
    assert "spec" in snapshot                      # the viewer still reads the draft spec
    assert client.post(url, json={"expected_revision": 1, "recipe": FAST}).status_code == 409   # one job at a time
    assert client.post("/api/projects/nope/forged-builds", json={"expected_revision": 1}).status_code in (404, 409)


def test_presets_endpoint_returns_defaults_and_valid_recipes(client):
    data = client.get("/api/forged/presets").json()
    assert data["defaults"]["family"] == "y_split" and data["defaults"]["spokes"] == 6
    ids = [p["id"] for p in data["presets"]]
    assert {"hf6-y-split", "tree6-branching", "work6-tapered"} <= set(ids)
    for preset in data["presets"]:
        assert recipe_from_dict(preset["recipe"]).spokes >= 3        # every preset is a full, valid recipe
        assert not any(key.startswith("_") for key in preset["recipe"])
    tree = next(p for p in data["presets"] if p["id"] == "tree6-branching")["recipe"]
    assert tree["family"] == "skeleton" and len(tree["skeleton"]["edges"]) == 7
