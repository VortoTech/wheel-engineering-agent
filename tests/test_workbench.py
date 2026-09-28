import json

from fastapi.testclient import TestClient


def make_run(tmp_path, recipe):
    run = tmp_path / "m1"
    (run / "machining").mkdir(parents=True)
    (run / "machining" / "recipe.json").write_text(json.dumps(recipe))
    (run / "chain.json").write_text(json.dumps({"case": "case-00/x", "spec": {}, "steps": []}))
    return run


def test_workbench_serves_runs_and_guards_paths(tmp_path):
    from wheelcam.workbench import create_app
    make_run(tmp_path, {})
    (tmp_path / "secret.txt").write_text("no")
    c = TestClient(create_app(tmp_path))
    assert c.get("/api/runs").json() == ["m1"]
    assert c.get("/api/runs/m1").json()["glb"] == "reconstruct/cad/wheel.glb"
    assert c.get("/files/m1/chain.json").status_code == 200
    assert c.get("/files/m1/../secret.txt").status_code == 404
    assert c.get("/api/runs/..").status_code == 404


def test_workbench_chat_keeps_history_and_the_edited_model(tmp_path, monkeypatch):
    import wheelcam.recipe_chat as rc
    from test_mesh_build import outline_recipe
    from wheelcam.workbench import create_app
    make_run(tmp_path, outline_recipe())
    monkeypatch.setattr(rc, "ask_model", lambda recipe, message, history=(): {
        "changes": [{"param": "spoke_pad_depth", "value": 8}, {"param": "pcd", "value": 120}], "reply": "", "refused": []})
    monkeypatch.setattr(rc.turn, "__defaults__", (None, (), rc.ask_model))
    c = TestClient(create_app(tmp_path))
    entry = c.post("/api/runs/m1/chat", json={"message": "脊高一点，PCD 120"}).json()
    assert entry["dir"] == "01" and entry["passed"] and "PCD" in entry["summary"]
    assert c.get("/api/runs/m1").json()["glb"] == "chat/01/wheel.glb"
    assert c.get("/files/m1/chat/01/wheel.glb").status_code == 200


def test_workbench_text_entry_creates_a_run(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from wheelcam.workbench import create_app

    def fake_chain(command, **kwargs):
        assert "--preview-only" in command
        out = Path(command[command.index("--out") + 1])
        out.mkdir()
        (out / "chain.json").write_text(json.dumps({"text": "做一个轮毂", "spec": {},
                                                     "steps": [{"step": "reconstruct", "ok": True}]}))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_chain)
    c = TestClient(create_app(tmp_path))
    result = c.post("/api/runs/text", json={"message": "做一个轮毂"})
    assert result.status_code == 200
    name = result.json()["name"]
    assert name.startswith("text-") and name in c.get("/api/runs").json()
    assert c.get(f"/api/runs/{name}").json()["chain"]["text"] == "做一个轮毂"
    assert "用文字新建" in c.get("/").text


def test_failed_chat_never_replaces_preview_and_original_recipe_is_used(tmp_path, monkeypatch):
    import wheelcam.recipe_chat as rc
    from wheelcam.workbench import create_app
    run = make_run(tmp_path, {"marker": "machining"})
    (run / "reconstruct").mkdir()
    (run / "reconstruct/recipe.json").write_text(json.dumps({"marker": "visual"}))
    calls = []

    def failed(recipe, message, out, spec, history):
        calls.append(recipe)
        out.mkdir(parents=True)
        (out / "recipe.json").write_text('{}')
        return {"summary": "校验未通过", "built": {"passed": False}, "refused": [], "changed": {}}

    monkeypatch.setattr(rc, "turn", failed)
    c = TestClient(create_app(tmp_path))
    result = c.post("/api/runs/m1/chat", json={"message": "脊高一点"}).json()
    assert calls == [{"marker": "visual"}]
    assert result["dir"] is None and "未采用" in result["summary"]
    state = c.get("/api/runs/m1").json()
    assert state["current_preview"]["downstream_stale"] is False
    assert state["glb"] == "reconstruct/cad/wheel.glb"


def test_active_preview_checks_and_stale_artifacts_reset(tmp_path):
    from wheelcam.workbench import create_app
    run = make_run(tmp_path, {})
    (run / "chat/01").mkdir(parents=True)
    checks = {"offset_et": {"pass": True, "measured_mm": 15}}
    (run / "chat/01/report.json").write_text(json.dumps({"checks": checks}))
    (run / "chat/history.json").write_text(json.dumps([{"dir": "01", "message": "改造型", "summary": "已修改"}]))
    c = TestClient(create_app(tmp_path))
    state = c.get("/api/runs/m1").json()
    assert state["current_preview"]["checks"] == checks
    assert state["current_preview"]["downstream_stale"] is True
    assert state["glb"] == "chat/01/wheel.glb"
    assert c.post("/api/runs/m1/chat/reset").status_code == 200
    state = c.get("/api/runs/m1").json()
    assert state["current_preview"]["downstream_stale"] is False
    assert state["glb"] == "reconstruct/cad/wheel.glb"


def test_workbench_render_dependencies_are_served_locally(tmp_path):
    from wheelcam.workbench import create_app
    c = TestClient(create_app(tmp_path))
    assert 'https://cdn.jsdelivr.net' not in c.get('/').text
    for path in ('build/three.module.js', 'examples/jsm/controls/OrbitControls.js',
                 'examples/jsm/loaders/GLTFLoader.js', 'examples/jsm/utils/BufferGeometryUtils.js',
                 'examples/jsm/environments/RoomEnvironment.js'):
        response = c.get('/static/three/' + path)
        assert response.status_code == 200
        assert 'javascript' in response.headers['content-type']
