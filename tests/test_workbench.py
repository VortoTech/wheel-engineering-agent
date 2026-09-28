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
