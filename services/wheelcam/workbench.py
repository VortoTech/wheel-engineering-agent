"""Demo workbench: one page per demo chain run (scripts/demo_chain.py) with a 3D view, the checks, the
CAD comparison, the drawing, the machining package and a conversation that edits the style.

    PYTHONPATH=services .venv/bin/python -m wheelcam.workbench [--runs runs/demo] [--port 8790]

Binds 127.0.0.1 only; on Spark it is reached through an SSH tunnel. The conversation uses
wheelcam.recipe_chat (WHEELCAM_CHAT_* model); every accepted edit is rebuilt and re-checked, and
its recipe, GLB and report are kept under <run>/chat/NN/.
"""
import argparse
import io
import threading
from contextlib import contextmanager
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .wheel_skill_contract import WheelInputSpec
from .workbench_revision import digest, confirm_spec, snapshot_report

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=400)


class TextIn(BaseModel):
    message: str = Field(min_length=1, max_length=800)


class StyleIn(BaseModel):
    model_config = {"extra": "forbid"}
    expected_recipe_sha256: str
    lip_pockets: int = Field(strict=True, ge=0, le=40)


class RevisionIn(BaseModel):
    expected_recipe_sha256: str
    confirmed: bool = False
    spec: WheelInputSpec = Field(default_factory=WheelInputSpec)
    hole_form: str | None = Field(default=None, max_length=40)


def create_app(runs: Path) -> FastAPI:
    runs = Path(runs).resolve()
    app = FastAPI(title="WheelCAM workbench", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    locks = {}
    locks_guard = threading.Lock()

    @contextmanager
    def edit_lock(name):
        with locks_guard:
            lock = locks.setdefault(name, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "这个版本正在修改或导出，请完成后重试")
        try:
            yield
        finally:
            lock.release()

    def execute(name, args):
        root = Path(__file__).resolve().parents[2]
        cmd = [sys.executable, str(root / "scripts/demo_chain.py"), *args, "--out", str(runs / name)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "任务超时，原版本保留；请检查运行日志")
        chain_path = runs / name / "chain.json"
        if result.returncode or not chain_path.exists():
            raise HTTPException(502, "生成失败，原版本保留；请检查服务端日志")
        chain = read(chain_path)
        first = (chain.get("steps") or [{}])[0]
        if not first.get("ok"):
            raise HTTPException(422, first.get("error", "重建失败"))
        return {"name": name, "chain": chain}

    def current_recipe(d):
        _, current = chat_state(d)
        return read(current / "recipe.json") if current else (
            read(d / "reconstruct/recipe.json") or read(d / "machining/recipe.json"))

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

    @app.get("/agent", response_class=HTMLResponse)
    @app.get("/", response_class=HTMLResponse)
    def page():
        return (Path(__file__).parent / "workbench.html").read_text()

    @app.get("/api/capabilities")
    def capabilities():
        configured = all(os.getenv(f"WHEELCAM_CHAT_{key}") or os.getenv(f"WHEELCAM_AGENT_{key}")
                         for key in ("BASE_URL", "MODEL"))
        return {"chat_configured": configured, "manual_style": True}

    @app.get("/api/runs")
    def list_runs():
        return [p.parent.name for p in sorted(runs.glob("*/chain.json"))
                if not (read(p).get("steps") or [{}])[0].get("ok") is False]

    @app.post("/api/runs/text")
    def create_from_text(body: TextIn):
        name = f"text-{uuid.uuid4().hex[:12]}"
        runs.mkdir(parents=True, exist_ok=True)
        return execute(name, ["--text", body.message, "--preview-only"])

    @app.post("/api/runs/image")
    def create_from_image(front: UploadFile = File(...), oblique: UploadFile | None = File(None),
                          spec_json: str = Form("{}"), hole_form: str = Form("")):
        from PIL import Image, ImageOps, UnidentifiedImageError
        from .forged_blank import hole_form as parse_form
        try:
            spec = WheelInputSpec.model_validate_json(spec_json).model_dump(exclude_none=True)
            if hole_form and not parse_form(hole_form):
                raise ValueError("孔型应为例如 15X32X60")
        except ValueError as e:
            raise HTTPException(422, str(e)[:300])
        name = f"image-{uuid.uuid4().hex[:12]}"
        source = runs / ".inputs" / name
        source.mkdir(parents=True)
        for label, upload in (("front", front), ("oblique", oblique)):
            if upload is None:
                continue
            raw = upload.file.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise HTTPException(413, "每张图片请小于 10 MB")
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in {"JPEG", "PNG", "WEBP"} or im.width * im.height > 30_000_000:
                        raise ValueError("unsupported image")
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((2400, 2400))
                    im.save(source / f"{label}.jpg", "JPEG", quality=95)
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                raise HTTPException(422, "请选择有效的 JPG、PNG 或 WebP 图片（不超过 3000 万像素）")
        (source / "spec.json").write_text(json.dumps({"spec": spec, "hole_form": hole_form or None,
            "spec_evidence": {k: {"source": "user"} for k in spec}, "style_agent": False, "hole_form_source": "user"}))
        return execute(name, [str(source), "--preview-only"])

    def revise(name, body, delivery, style=None):
        d = run_dir(name)
        with edit_lock(name):
            recipe = current_recipe(d)
            if digest(recipe) != body.expected_recipe_sha256:
                raise HTTPException(409, "模型版本已改变，请刷新后重新确认")
            chain = read(d / "chain.json")
            spec, form = chain.get("spec", {}), chain.get("hole_form")
            changes = body.spec.model_dump(exclude_none=True) if style is None else {}
            if delivery and (changes or body.hole_form is not None):
                raise HTTPException(422, "请先确认工程尺寸，再生成交付包")
            if style is None and not delivery and (not body.confirmed or not (changes or body.hole_form)):
                raise HTTPException(422, "请明确确认本次工程尺寸修改")
            hole_form = getattr(body, "hole_form", None)
            if hole_form is not None:
                form = hole_form
            try:
                before = recipe
                if style is not None:
                    from .recipe_chat import apply_edit
                    recipe = apply_edit(recipe, {"lip_pockets": style})
                elif not delivery:
                    recipe, spec = confirm_spec(recipe, spec, changes, form)
                original = read(d / "reconstruct/engineering_report.json") or {}
                history, _ = chat_state(d)
                for entry in history:
                    if entry.get("dir"):
                        for field in entry.get("changed", {}) or {}:
                            if field in recipe:
                                original.setdefault("parameters", {})[field] = {
                                    "value": recipe[field], "source": "agent", "note": entry.get("message", "造型对话修改")}
                report = snapshot_report(original, recipe, spec, changes, form, hole_form is not None)
                if style is not None:
                    report["parameters"]["lip_pockets"] = {"value": style, "source": "user",
                        "note": "工作台手动确认造型目标；不是照片自动识别结果"}
                    report["parameters"]["lip_pocket_r"] = {"value": recipe["lip_pocket_r"], "source": "rule",
                        "note": "由盲窗数量修改联动设置径向范围"}
                    report.setdefault("questions", []).append("外圈盲窗深度采用模板假设，须工程师确认；盲窗不是轮辋贯穿孔。")
            except ValueError as e:
                raise HTTPException(422, str(e)[:300])
            child = f"{'style' if style is not None else 'delivery' if delivery else 'revision'}-{uuid.uuid4().hex[:12]}"
            source = runs / ".inputs" / child
            source.mkdir(parents=True)
            payload = {"recipe": recipe, "recipe_sha256": digest(recipe), "spec": spec, "hole_form": form,
                       "report": report, "reference": str(d / "reference"),
                       "parent": {"run": name, "recipe_sha256": body.expected_recipe_sha256,
                                  "changes": changes, "hole_form_confirmed": hole_form is not None,
                                  "style_changes": {"lip_pockets": {"from": before.get("lip_pockets", 0), "to": style}} if style is not None else {}},
                       "source_text": chain.get("text")}
            snapshot = source / "snapshot.json"
            snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
            return execute(child, ["--snapshot", str(snapshot)] + ([] if delivery else ["--preview-only"]))

    @app.post("/api/runs/{name}/style")
    def edit_style(name: str, body: StyleIn):
        return revise(name, body, False, style=body.lip_pockets)

    @app.post("/api/runs/{name}/spec")
    def confirm_dimensions(name: str, body: RevisionIn):
        return revise(name, body, False)

    @app.post("/api/runs/{name}/deliver")
    def deliver(name: str, body: RevisionIn):
        return revise(name, body, True)

    @app.get("/api/runs/{name}")
    def get_run(name: str):
        d = run_dir(name)
        history, current = chat_state(d)
        package = read(d / "package" / "process_plan.json") or {}
        original = read(d / "reconstruct" / "engineering_report.json") or {}
        current_report = read(current / "report.json") if current else None
        return {"name": name, "chain": read(d / "chain.json"), "compare": read(d / "compare.json"),
                "machining": read(d / "machining" / "machining_report.json"),
                "engineering_report": original,
                "style_parameters": {k: (current_recipe(d) or {}).get(k) for k in
                                     ("lip_pockets", "lip_pocket_r", "spokes", "flank_w", "spoke_pad_depth")},
                "reconstruct": {k: v for k, v in original.items()
                                if k in ("readiness", "readiness_limits", "checks", "questions", "unknown", "engineering_understanding")},
                "current_preview": {"revision": f"chat/{current.name}" if current else "reconstruct",
                                    "checks": (current_report or original).get("checks", {}),
                                    "readiness": "L0", "downstream_stale": current is not None or bool((read(d / "chain.json") or {}).get("preview_only")),
                                    "recipe_sha256": digest(current_recipe(d))},
                "simulation": package.get("simulation") or package.get("simulation_3d"),
                "glb": (f"chat/{current.name}/wheel.glb" if current else
                        "reconstruct/wheel.glb" if (d / "reconstruct" / "wheel.glb").exists() else
                        "reconstruct/cad/wheel.glb"),
                "text_report": read(d / "reconstruct" / "engineering_report.json") if
                               (read(d / "chain.json") or {}).get("text") else None,
                "style_agent": read(d / "reconstruct" / "style" / "style_agent.json"),
                "style_comparison": read(d / "style_comparison.json"),
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
        with edit_lock(name):
            d = run_dir(name)
            history, current = chat_state(d)
            recipe = read(current / "recipe.json") if current else (
                read(d / "reconstruct" / "recipe.json") or read(d / "machining" / "recipe.json"))
            spec = (read(d / "chain.json") or {}).get("spec")
            n = len(history) + 1
            convo = [m for h in history[-4:] for m in ({"role": "user", "content": h["message"]},
                                                        {"role": "assistant", "content": h["summary"]})]
            try:
                result = turn(recipe, body.message, d / "chat" / f"{n:02d}", spec, history=convo)
            except Exception as e:                   # noqa: BLE001 - the model or the build; say which
                raise HTTPException(502, f"{type(e).__name__}: {e}"[:300])
            passed = (result["built"] or {}).get("passed") is True
            entry = {"message": body.message, "summary": result["summary"], "changed": result.get("changed"),
                     "refused": result["refused"], "passed": (result["built"] or {}).get("passed"),
                     "dir": f"{n:02d}" if passed else None}
            if result["built"] and not passed:
                entry["summary"] += " 本次修改未采用，继续显示上一版通过校验的模型。"
            history.append(entry)
            (d / "chat").mkdir(exist_ok=True)
            (d / "chat" / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=1))
            return entry

    @app.post("/api/runs/{name}/chat/reset")
    def reset(name: str):
        with edit_lock(name):
            d = run_dir(name)
            history, _ = chat_state(d)
            history.append({"message": "（恢复到重建结果）", "summary": "已恢复到重建结果。", "reset": True, "dir": None})
            (d / "chat").mkdir(exist_ok=True)
            # A reset ends the edit chain; later turns start from the original visual recipe.
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
