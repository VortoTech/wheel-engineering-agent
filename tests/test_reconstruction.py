import io
import json
import sys

from fastapi.testclient import TestClient
from PIL import Image

from wheelcam.app import create_app
from wheelcam.sf3d import prepare_input
from wheelcam.worker import Worker


def image_bytes():
    result = io.BytesIO()
    Image.new("RGB", (64, 64), "gray").save(result, "PNG")
    return result.getvalue()


def test_white_studio_background_preserves_wheel_openings(tmp_path):
    source = tmp_path / "wheel.png"
    target = tmp_path / "prepared.png"
    image = Image.new("RGB", (32, 32), "white")
    for x in range(6, 26):
        for y in range(6, 26):
            if x in {6, 7, 24, 25} or y in {6, 7, 24, 25}:
                image.putpixel((x, y), (35, 35, 35))
    image.save(source)

    prepared, metadata = prepare_input(source, target)

    assert prepared == target
    assert metadata["mode"] == "white_background_alpha"
    alpha = Image.open(target).getchannel("A")
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((16, 16)) == 0
    assert alpha.getpixel((6, 16)) == 255


def test_reconstruction_requires_config_and_primary_image(tmp_path, monkeypatch):
    monkeypatch.delenv("WHEELCAM_SF3D_ROOT", raising=False)
    monkeypatch.delenv("WHEELCAM_SF3D_PYTHON", raising=False)
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        project = client.post("/api/projects", json={"name": "视觉测试"}).json()
        state = client.get("/api/reconstruction/status").json()
        assert state["available"] is False
        response = client.post(f'/api/projects/{project["id"]}/reconstructions',
                               json={"expected_revision": project["revision"]})
        assert response.status_code == 503
        assert "WHEELCAM_SF3D_ROOT" in response.json()["detail"]


def test_visual_reconstruction_is_separate_from_cad_outputs(tmp_path, monkeypatch):
    sf3d_root = tmp_path / "sf3d"
    sf3d_root.mkdir()
    (sf3d_root / "run.py").write_text(
        """import argparse
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('image'); p.add_argument('--output-dir'); p.add_argument('--device')
p.add_argument('--pretrained-model'); p.add_argument('--texture-resolution'); p.add_argument('--remesh_option')
a=p.parse_args(); out=Path(a.output_dir)/'0'; out.mkdir(parents=True); (out/'mesh.glb').write_bytes(b'glTF-test')
""")
    monkeypatch.setenv("WHEELCAM_SF3D_ROOT", str(sf3d_root))
    monkeypatch.setenv("WHEELCAM_SF3D_PYTHON", sys.executable)
    monkeypatch.setenv("WHEELCAM_SF3D_DEVICE", "mps")
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        project = client.post("/api/projects", json={"name": "视觉测试"}).json()
        project = client.post(f'/api/projects/{project["id"]}/images',
                              files={"file": ("wheel.png", image_bytes(), "image/png")}).json()
        queued = client.post(f'/api/projects/{project["id"]}/reconstructions',
                             json={"expected_revision": project["revision"]})
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        with client.app.state.store.connection() as db:
            job = dict(db.execute("SELECT * FROM reconstruction_jobs WHERE id=?", (job_id,)).fetchone())
        Worker(client.app.state.store).run_reconstruction(job)

        saved = client.get(f'/api/projects/{project["id"]}').json()
        visual = saved["reconstructions"][0]
        assert visual["status"] == "succeeded"
        assert visual["snapshot"]["usage"] == "visual_reference_only"
        assert visual["report"]["device"] == "mps"
        assert visual["report"]["artifact"]["bytes"] == 9
        assert saved["jobs"] == []
        assert client.get(f"/api/reconstructions/{job_id}/glb").content == b"glTF-test"
        assert client.get(f"/api/reconstructions/{job_id}/report").json()["usage"] == "visual_reference_only"
        assert client.get(f"/api/reconstructions/{job_id}/step").status_code == 404
        assert client.get(f"/api/builds/{job_id}/glb").status_code == 404
        log = (tmp_path / "reconstructions" / job_id / "reconstruction.log").read_text()
        assert log == ""


def test_reconstruction_snapshot_conflict_and_single_active_job(tmp_path, monkeypatch):
    sf3d_root = tmp_path / "sf3d"
    sf3d_root.mkdir()
    (sf3d_root / "run.py").write_text("# availability probe only")
    monkeypatch.setenv("WHEELCAM_SF3D_ROOT", str(sf3d_root))
    monkeypatch.setenv("WHEELCAM_SF3D_PYTHON", sys.executable)
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        project = client.post("/api/projects", json={"name": "视觉测试"}).json()
        project = client.post(f'/api/projects/{project["id"]}/images',
                              files={"file": ("wheel.png", image_bytes(), "image/png")}).json()
        url = f'/api/projects/{project["id"]}/reconstructions'
        assert client.post(url, json={"expected_revision": 1}).status_code == 409
        first = client.post(url, json={"expected_revision": project["revision"]})
        assert first.status_code == 202
        assert client.post(url, json={"expected_revision": project["revision"]}).status_code == 409
        snapshot = client.get(f'/api/projects/{project["id"]}').json()["reconstructions"][0]["snapshot"]
        assert snapshot["image_sha256"]
        assert "不生成 STEP" in snapshot["limitations"]
