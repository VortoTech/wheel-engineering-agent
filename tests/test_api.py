import io
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from wheelcam.app import create_app
from wheelcam.worker import Worker


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        yield client


def create(client):
    response = client.post("/api/projects", json={"name": "测试轮毂"})
    assert response.status_code == 201
    return response.json()


def draft(project, **changes):
    return {"name": project["name"], "spec": {**project["spec"], **changes},
            "sources": project["sources"], "expected_revision": project["revision"]}


def test_draft_conflict_and_restart_persistence(client, tmp_path):
    project = create(client)
    url = f'/api/projects/{project["id"]}'
    assert client.put(url, json=draft(project, spoke_count=8)).status_code == 200
    assert client.put(url, json=draft(project, spoke_count=5)).status_code == 409
    assert client.get(url).json()["spec"]["spoke_count"] == 8
    with TestClient(create_app(tmp_path, start_worker=False)) as reopened:
        assert reopened.get(url).json()["spec"]["spoke_count"] == 8


def test_validation_and_snapshot_immutability(client):
    project = create(client)
    url = f'/api/projects/{project["id"]}'
    assert client.put(url, json=draft(project, center_bore_mm=90, bolt_circle_mm=100)).status_code == 422
    assert client.get(url).json()["revision"] == 1
    queued = client.post(f'{url}/builds', json={"expected_revision": 1})
    assert queued.status_code == 202
    assert client.post(f'{url}/builds', json={"expected_revision": 1}).status_code == 409
    assert client.put(url, json=draft(project, spoke_count=7)).status_code == 200
    fresh = client.get(url).json()
    assert fresh["jobs"][0]["snapshot"]["spec"]["spoke_count"] == 6
    assert fresh["spec"]["spoke_count"] == 7
    assert client.get(f'/api/builds/{queued.json()["id"]}/step').status_code == 404


def test_reference_image_validation_primary_and_snapshot(client, tmp_path):
    project = create(client)
    url = f'/api/projects/{project["id"]}'
    assert client.post(f'{url}/images', files={"file": ("fake.png", b"not an image", "image/png")}).status_code == 422
    image_bytes = io.BytesIO()
    Image.new("RGB", (100, 80), "gray").save(image_bytes, "PNG")
    response = client.post(f'{url}/images', files={"file": ("../reference.png", image_bytes.getvalue(), "image/png")})
    assert response.status_code == 201
    updated = response.json()
    reference = updated["images"][0]
    assert reference["name"] == "reference.png"
    assert len(reference["sha256"]) == 64
    assert updated["primary_image_id"] == reference["id"]
    assert (tmp_path / "images" / (reference["id"] + ".source")).read_bytes() == image_bytes.getvalue()
    assert client.get(f'/api/images/{reference["id"]}').headers["content-type"] == "image/jpeg"
    assert client.post(f'{url}/builds', json={"expected_revision": 1}).status_code == 409
    assert client.post(f'{url}/builds', json={"expected_revision": updated["revision"]}).status_code == 202
    snapshot = client.get(url).json()["jobs"][0]["snapshot"]
    assert snapshot["reference_images"][0]["sha256"] == reference["sha256"]
    assert snapshot["image_usage"] == "manual_reference_only"


def test_local_write_origin_and_missing_artifacts(client):
    assert client.post("/api/projects", json={"name": "remote"}, headers={"origin": "https://example.com"}).status_code == 403
    assert client.get("/api/projects/missing").status_code == 404
    assert client.get("/api/builds/missing/step").status_code == 404
    assert client.get("/api/builds/missing/build.log").status_code == 404


def test_worker_failure_does_not_replace_previous_model(client, monkeypatch):
    project = create(client)
    store = client.app.state.store
    old = store.enqueue(project["id"], 1)
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='succeeded',report=? WHERE id=?", (json.dumps({"previous": True}), old))
    old_dir = store.root / "models" / old
    old_dir.mkdir()
    (old_dir / "wheel.step").write_text("previous-file")
    failed = store.enqueue(project["id"], 1)

    class BadProcess:
        def __init__(self, *args, **kwargs): pass
        def poll(self): return 1
        def wait(self, **kwargs): return 1

    monkeypatch.setattr("wheelcam.worker.subprocess.Popen", BadProcess)
    with store.connection() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (failed,)).fetchone())
    Worker(store).run(job)
    history = store.project(project["id"])["jobs"]
    assert history[0]["status"] == "failed"
    assert history[1]["status"] == "succeeded"
    assert client.get(f'/api/builds/{old}/step').text == "previous-file"


def test_interrupted_job_recovered_as_failed(client, monkeypatch):
    project = create(client)
    store = client.app.state.store
    interrupted = store.enqueue(project["id"], 1)
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (interrupted,))
    worker = Worker(store)
    monkeypatch.setattr(worker.thread, "start", lambda: None)
    worker.start()
    assert store.project(project["id"])["jobs"][0]["status"] == "failed"
    worker.stop()


def test_second_worker_does_not_invalidate_running_job(client, monkeypatch):
    store = client.app.state.store
    project = create(client)
    first = Worker(store)
    monkeypatch.setattr(first.thread, "start", lambda: None)
    first.start()
    job_id = store.enqueue(project["id"], 1)
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    try:
        with pytest.raises(RuntimeError, match="已有 WheelCAM 服务"):
            Worker(store).start()
        assert store.project(project["id"])["jobs"][0]["status"] == "running"
    finally:
        first.stop()


@pytest.mark.parametrize("ready", [False, True])
def test_worker_times_out_initialization_and_geometry_separately(client, monkeypatch, ready):
    project = create(client)
    store = client.app.state.store
    job_id = store.enqueue(project["id"], 1)
    killed = []

    class HangingProcess:
        def __init__(self, args, **kwargs):
            if ready:
                (Path(args[-1]) / "kernel.ready").touch()
        def poll(self): return None
        def wait(self, timeout=None):
            if not killed:
                raise subprocess.TimeoutExpired("geometry", timeout)
            return -9
        def kill(self): killed.append(True)

    monkeypatch.setattr("wheelcam.worker.subprocess.Popen", HangingProcess)
    with store.connection() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    Worker(store, timeout=1, initialization_timeout=0).run(job)
    result = store.project(project["id"])["jobs"][0]
    assert killed == [True]
    assert result["status"] == "failed"
    assert ("实体生成超过时间限制" if ready else "内核初始化超时") in result["error"]
    assert client.get(f'/api/builds/{job_id}/step').status_code == 404


def test_preparation_persists_snapshots_and_omission_does_not_erase(client, tmp_path):
    project = create(client)
    url = f'/api/projects/{project["id"]}'
    preparation = {'stock': {'outer_diameter_mm': 540}, 'material': {'name': 'test', 'density_kg_m3': 2700}}
    changed = client.put(url, json={**draft(project), 'preparation': preparation})
    assert changed.status_code == 200
    saved = changed.json()
    queued = client.post(f'{url}/builds', json={'expected_revision': saved['revision']}).json()
    # An older client that omits preparation must not clear it.
    unchanged = client.put(url, json=draft(saved, spoke_count=7)).json()
    assert unchanged['preparation'] == saved['preparation']
    cleared = client.put(url, json={**draft(unchanged), 'preparation': {}}).json()
    assert cleared['preparation']['stock'] is None
    assert cleared['jobs'][0]['snapshot']['preparation'] == saved['preparation']
    assert cleared['jobs'][0]['snapshot']['model_id'] == queued['id']
    with TestClient(create_app(tmp_path, start_worker=False)) as reopened:
        assert reopened.get(url).json()['jobs'][0]['snapshot']['preparation'] == saved['preparation']


def test_preparation_validation_and_download_routes(client):
    project = create(client)
    url = f'/api/projects/{project["id"]}'
    assert client.put(url, json={**draft(project), 'preparation': {'material': {'name': 'bad', 'density_kg_m3': -1}}}).status_code == 422
    store = client.app.state.store
    job = store.enqueue(project['id'], 1)
    directory = store.root / 'models' / job
    directory.mkdir()
    for route, filename in [('features', 'features.json'), ('operations', 'operations.csv'), ('handoff', 'handoff.zip'), ('stock', 'stock.step'), ('caliper', 'caliper-envelope.step')]:
        (directory / filename).write_bytes(b'test')
        assert client.get(f'/api/builds/{job}/{route}').status_code == 404
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job,))
    for route in ['features', 'operations', 'handoff', 'stock', 'caliper']:
        assert client.get(f'/api/builds/{job}/{route}').content == b'test'
    (directory / 'stock.step').unlink()
    assert client.get(f'/api/builds/{job}/stock').status_code == 404


def test_same_origin_custom_port_and_untrusted_host(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False), base_url='http://127.0.0.1:18766') as local:
        assert local.post('/api/projects', json={'name': 'custom port'}, headers={'origin': 'http://127.0.0.1:18766'}).status_code == 201
        assert local.post('/api/projects', json={'name': 'bad origin'}, headers={'origin': 'https://example.com'}).status_code == 403
        assert local.post('/api/projects', json={'name': 'bad host'}, headers={'origin': 'http://evil.example', 'host': 'evil.example'}).status_code == 400


def test_photo_preset_creates_separate_editable_project(client):
    original = create(client)
    paired = client.post('/api/projects', json={'name': '照片双辐', 'preset': 'photo-paired-8'})
    assert paired.status_code == 201
    project = paired.json()
    assert project['id'] != original['id']
    assert project['spec']['spoke_style'] == 'paired' and project['spec']['spoke_count'] == 8
    assert '非照片测量' in project['sources']['rim_diameter_in']['note']
    assert client.get(f'/api/projects/{original["id"]}').json()['spec']['spoke_style'] == 'single'
    assert client.post('/api/projects', json={'preset': 'unknown'}).status_code == 422
