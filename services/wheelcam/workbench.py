"""Demo workbench: one page per demo chain run (scripts/demo_chain.py) with a 3D view, the checks, the
CAD comparison, the drawing, the machining package and a conversation that edits the style.

    PYTHONPATH=services .venv/bin/python -m wheelcam.workbench [--runs runs/demo] [--port 8790]

Binds 127.0.0.1 only; on Spark it is reached through an SSH tunnel. The conversation uses
wheelcam.recipe_chat (WHEELCAM_CHAT_* model); every accepted edit is rebuilt and re-checked, and
its recipe, GLB and report are kept under <run>/chat/NN/.
"""
import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=400)


class TextIn(BaseModel):
    message: str = Field(min_length=1, max_length=800)


def create_app(runs: Path) -> FastAPI:
    runs = Path(runs).resolve()
    app = FastAPI(title="WheelCAM workbench", docs_url=None, redoc_url=None)

    def run_dir(name: str) -> Path:
        if not NAME.match(name) or not (runs / name / "chain.json").exists():
            raise HTTPException(404, "no such run")
        return runs / name

    def read(path: Path):
        return json.loads(path.read_text()) if path.exists() else None

    def chat_state(d: Path):
        history = read(d / "chat" / "history.json") or []
        last = next((h for h in reversed(history) if h.get("dir")), None)
        return history, (d / "chat" / last["dir"]) if last else None

    @app.get("/", response_class=HTMLResponse)
    def page():
        return (Path(__file__).parent / "workbench.html").read_text()

    @app.get("/api/runs")
    def list_runs():
        return [p.parent.name for p in sorted(runs.glob("*/chain.json"))]

    @app.post("/api/runs/text")
    def create_from_text(body: TextIn):
        name = f"text-{uuid.uuid4().hex[:12]}"
        runs.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[2]
        cmd = [sys.executable, str(root / "scripts/demo_chain.py"), "--text", body.message,
               "--out", str(runs / name)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "文字生成超时；请检查模型服务与运行日志")
        if result.returncode or not (runs / name / "chain.json").exists():
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise HTTPException(502, (detail[-1] if detail else "文字生成失败")[:300])
        chain = read(runs / name / "chain.json")
        if not chain["steps"] or not chain["steps"][0]["ok"]:
            raise HTTPException(502, chain["steps"][0].get("error", "重建失败"))
        return {"name": name, "chain": chain}

    @app.get("/api/runs/{name}")
    def get_run(name: str):
        d = run_dir(name)
        history, current = chat_state(d)
        package = read(d / "package" / "process_plan.json") or {}
        return {"name": name, "chain": read(d / "chain.json"), "compare": read(d / "compare.json"),
                "machining": read(d / "machining" / "machining_report.json"),
                "reconstruct": {k: v for k, v in (read(d / "reconstruct" / "engineering_report.json") or {}).items()
                                if k in ("readiness", "readiness_limits", "checks", "questions", "unknown")},
                "simulation": package.get("simulation") or package.get("simulation_3d"),
                "glb": (f"chat/{current.name}/wheel.glb" if current else
                        "reconstruct/wheel.glb" if (d / "reconstruct" / "wheel.glb").exists() else
                        "reconstruct/cad/wheel.glb"),
                "text_report": read(d / "reconstruct" / "engineering_report.json") if
                               (read(d / "chain.json") or {}).get("text") else None,
                "style_agent": read(d / "reconstruct" / "style" / "style_agent.json"),
                "references": sorted(p.name for p in (d / "reference").glob("*.jpg")),
                "history": history}

    @app.get("/files/{name}/{path:path}")
    def files(name: str, path: str):
        d = run_dir(name)
        target = (d / path).resolve()
        if not target.is_relative_to(d) or not target.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(target)

    @app.post("/api/runs/{name}/chat")
    def chat(name: str, body: ChatIn):
        from .recipe_chat import turn
        d = run_dir(name)
        history, current = chat_state(d)
        recipe = read((current or d / "machining") / "recipe.json")
        spec = (read(d / "chain.json") or {}).get("spec")
        n = len(history) + 1
        convo = [m for h in history[-4:] for m in ({"role": "user", "content": h["message"]},
                                                    {"role": "assistant", "content": h["summary"]})]
        try:
            result = turn(recipe, body.message, d / "chat" / f"{n:02d}", spec, history=convo)
        except Exception as e:                   # noqa: BLE001 - the model or the build; say which
            raise HTTPException(502, f"{type(e).__name__}: {e}"[:300])
        entry = {"message": body.message, "summary": result["summary"], "changed": result.get("changed"),
                 "refused": result["refused"], "passed": (result["built"] or {}).get("passed"),
                 "dir": f"{n:02d}" if result["built"] else None}
        history.append(entry)
        (d / "chat").mkdir(exist_ok=True)
        (d / "chat" / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=1))
        return entry

    @app.post("/api/runs/{name}/chat/reset")
    def reset(name: str):
        d = run_dir(name)
        history, _ = chat_state(d)
        history.append({"message": "（恢复到重建结果）", "summary": "已恢复到重建结果。", "reset": True, "dir": None})
        (d / "chat").mkdir(exist_ok=True)
        # a reset entry ends the edit chain: later turns start from the machining recipe again
        (d / "chat" / "history.json").write_text(json.dumps(
            [{**h, "dir": None} if h.get("dir") else h for h in history], ensure_ascii=False, indent=1))
        return {"ok": True}

    return app


def main():
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs/demo")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args()
    uvicorn.run(create_app(Path(a.runs)), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
