"""The whole demo chain for one scrubbed real order, in one command.

    PYTHONPATH=services .venv/bin/python scripts/demo_chain.py runs/real-orders/case-03/d20w10.5 --out runs/demo/m59

1 reconstruct   wheel_skill on the order render + confirmed specs (mesh kernel, seconds)
                (with WHEELCAM_VLM_BASE_URL set, the skill's style agent corrects the preset styling)
2 machining     machining-level STEP pair (stock + part) with the order's hole form (wheelcam.machining_step)
3 package       process plan, reference NC and cutting simulation on that STEP pair
                (scripts/build_manufacturing_step_demo.py)
4 drawing       A3 engineering drawing (SVG; PDF where headless Chrome is installed)
5 compare       the machining STEP's section and dimensions against the factory CAD (truth.json)
Writes chain.json with each step's time, status and outputs; the workbench (wheelcam.workbench) shows it.
Every output is a draft for engineering review: manufacturing_status not_released.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "experiments/real-orders"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?", type=Path, help="runs/real-orders/case-XX/<size>")
    ap.add_argument("--text", help="从文字新建轮毂，替代订单目录")
    ap.add_argument("--snapshot", type=Path, help="已确认配方快照 JSON；不重新调用模型")
    ap.add_argument("--front", type=Path, help="正面图；与 --spec 一起替代订单目录（写入 <out>/input/）")
    ap.add_argument("--oblique", type=Path, help="可选斜视图（配合 --front）")
    ap.add_argument("--spec", help='已知规格 JSON，例如 {"diameter_in":20,"width_in":9,"pcd_mm":114.3,"bolts":5,"center_bore_mm":73.1,"et_mm":35}')
    ap.add_argument("--spec-evidence", help='规格来源 JSON，例如 {"pcd_mm":{"source":"drawing","reference":"确认单"}}；未给的记为 unspecified')
    ap.add_argument("--hole-form", help="确认单孔型，例如 15X32X60")
    ap.add_argument("--preview-only", action="store_true", help="只重建并校验预览")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if sum(map(bool, (a.case, a.text, a.snapshot, a.front))) != 1:
        ap.error("请选择订单目录、--front、--text 或 --snapshot 其中一个输入")
    if a.front and not a.spec:
        ap.error("--front 需要 --spec（未知的规格可以不写，但要用 JSON 对象给出已知项）")
    case, out = a.case, a.out
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; use a new directory")
    out.mkdir(parents=True, exist_ok=True)
    if a.front:                                  # an order directory built from the arguments, kept with the run
        from PIL import Image
        case = out / "input"
        case.mkdir()
        for src, name in ((a.front, "front.jpg"), (a.oblique, "oblique.jpg")):
            if src:
                Image.open(src).convert("RGB").save(case / name, "JPEG", quality=95)
        spec_in = json.loads(a.spec)
        evidence = json.loads(a.spec_evidence) if a.spec_evidence else {}
        (case / "spec.json").write_text(json.dumps({
            "spec": spec_in, "hole_form": a.hole_form,
            "spec_evidence": {k: evidence.get(k, {"source": "unspecified"}) for k in spec_in}},
            ensure_ascii=False, indent=1))
    snapshot = json.loads(a.snapshot.read_text()) if a.snapshot else None
    order = snapshot or (json.loads((case / "spec.json").read_text()) if case else {})
    reference = Path(snapshot["reference"]) if snapshot and snapshot.get("reference") else case
    if reference:
        import shutil
        (out / "reference").mkdir()
        for name in ("front.jpg", "oblique.jpg", "back.jpg"):
            if (reference / name).exists():
                shutil.copy(reference / name, out / "reference" / name)
    spec = order.get("spec", {})
    steps, t0 = [], time.time()

    def step(name, fn):
        t = time.time()
        try:
            detail = fn() or {}
            steps.append({"step": name, "ok": True, "seconds": round(time.time() - t, 1), **detail})
        except Exception as e:                   # noqa: BLE001 - the chain reports and goes on where it can
            steps.append({"step": name, "ok": False, "seconds": round(time.time() - t, 1),
                          "error": f"{type(e).__name__}: {e}"[:400]})
        print(json.dumps(steps[-1], ensure_ascii=False), flush=True)
        return steps[-1]["ok"]

    def reconstruct():
        if snapshot:
            from wheelcam.recipe_chat import rebuild
            from wheelcam.workbench_revision import digest
            recipe = snapshot["recipe"]
            if digest(recipe) != snapshot["recipe_sha256"]:
                raise ValueError("配方快照校验失败")
            result = rebuild(recipe, out / "reconstruct", spec)
            import hashlib
            report = {**snapshot["report"], "checks": result["checks"], "readiness": "L0",
                      "all_checks_pass": result["passed"], "mass_kg_6061": result["mass_kg_6061"],
                      "build_seconds": result["seconds"], "parent": snapshot.get("parent"),
                      "artifacts": {"wheel.glb": str(out / "reconstruct/wheel.glb")},
                      "output_evidence": {name: {"sha256": hashlib.sha256((out / "reconstruct" / name).read_bytes()).hexdigest(),
                                                   "bytes": (out / "reconstruct" / name).stat().st_size}
                                          for name in ("recipe.json", "wheel.glb", "report.json")}}
            for key in ("mesh_build", "visual_check", "style_agent", "style_agent_steps", "style_agent_status"):
                report.pop(key, None)  # old recipe's measurements must not certify this revision
            (out / "reconstruct/engineering_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
            (out / "input_spec.json").write_text(json.dumps({"spec": spec, "hole_form": order.get("hole_form")}))
            if not result["passed"]:
                raise ValueError("确认后的配方未通过网格校验；原版本保留")
            return {"source": "confirmed_snapshot", "readiness": "L0", "recipe_sha256": digest(recipe)}
        if a.text:
            from wheelcam.text_wheel import run as run_text
            r = run_text(a.text, out / "reconstruct")
            spec.update(r["spec"])
            order["hole_form"] = r["hole_form"]
            (out / "input_spec.json").write_text(json.dumps({"spec": spec, "hole_form": r["hole_form"]}))
            if any(not c.get("pass", True) for c in r["checks"].values()):
                raise ValueError("文字配方未通过网格校验")
            return {"readiness": r["readiness"], "spokes": r["parameters"]["spokes"]["value"],
                    "checks_failed": [k for k, v in r["checks"].items() if not v["pass"]],
                    "questions": r["questions"], "source": "text"}
        from wheelcam.wheel_skill import run
        oblique = case / "oblique.jpg"
        r = run(case / "front.jpg", spec, out / "reconstruct", oblique if oblique.exists() else None,
                kernel="mesh", spec_evidence=order.get("spec_evidence", {k: {"source": "drawing"} for k in spec}), hole_form=order.get("hole_form"),
                visual_check=order.get("visual_check", False),
                style_agent=order.get("style_agent", bool(os.getenv("WHEELCAM_VLM_BASE_URL"))))
        if order.get("hole_form_source") == "user" and order.get("hole_form"):
            for key in ("bolt_d", "seat_d", "seat_cone_deg"):
                r["parameters"][key]["source"] = "user"
                r["parameters"][key]["note"] = "工作台用户明确提供孔型"
                supplied = r.get("engineering_understanding", {}).get("evidence_groups", {}).get("supplied", {})
                if key in supplied:
                    supplied[key].update(r["parameters"][key])
            (out / "reconstruct/engineering_report.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
        if any(not c.get("pass", True) for c in r["checks"].values()):
            raise ValueError("图片重建未通过网格校验")
        return {"readiness": r["readiness"], "spokes": r["parameters"].get("spokes", {}).get("value"),
                "style_agent": r.get("style_agent_status"),
                "style_changed": {k: v["to"] for k, v in (r.get("style_agent") or {}).items() if k != "lip_pocket_r"},
                "checks_failed": [k for k, v in r["checks"].items() if not v.get("pass", True)]}

    def package():
        # the process plan, reference NC and simulation read the machining STEP pair (stock + part)
        done = subprocess.run([sys.executable, str(ROOT / "scripts/build_manufacturing_step_demo.py"),
                               "--machining-dir", str(out / "machining"), "--spec", str(case / "spec.json" if case else out / "input_spec.json"),
                               "--out", str(out / "package")], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "package failed")
        plan = json.loads((out / "package/process_plan.json").read_text())
        sim = plan.get("simulation_3d") or plan.get("simulation") or {}
        return {"simulation": sim.get("status"), "conclusion": sim.get("conclusion")}

    def design_recipe():
        return json.loads((out / "reconstruct/recipe.json").read_text())   # the skill's, style agent applied

    def machining():
        from wheelcam.machining_step import export
        r = export(design_recipe(), out / "machining",
                   order.get("hole_form"), spec.get("et_mm"))
        if any(c["pass"] is False for c in r["checks"].values()):
            raise ValueError("加工级 STEP 校验未通过，停止交付包生成")
        return {"checks_failed": [k for k, c in r["checks"].items() if c["pass"] is False],
                "adjustments": r["adjustments"]}

    def drawing():
        from wheelcam.drawing import drawing_svg
        from wheelcam.forged_blank import recipe_from_dict
        from wheelcam.mesh_build import build
        recipe = json.loads((out / "machining/recipe.json").read_text())   # the order's hole form applied
        body, _ = build(recipe)
        svg = drawing_svg(body, recipe_from_dict(recipe), spec=spec, order={},
                          drawing_no=case.parent.name.upper() if case else "TEXT-DRAFT",
                          source="参数来源见 engineering_report.json；工程草案，未经制造审核",
                          machining_report=json.loads((out / "machining/machining_report.json").read_text()))
        (out / "drawing.svg").write_text(svg)
        import shutil
        mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        found = [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
        chrome = mac if mac.exists() else Path(next((f for f in found if f), "")) if any(found) else None
        if chrome:                               # A3 PDF where a Chrome/Chromium is at hand; SVG otherwise
            html = out / "drawing.html"
            html.write_text('<!doctype html><meta charset="utf-8"><style>@page{size:420mm 297mm;margin:0}'
                            'body{margin:0}img{width:420mm;height:297mm}</style><img src="drawing.svg">')
            subprocess.run([str(chrome), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={(out / 'drawing.pdf').resolve()}", html.resolve().as_uri()], capture_output=True, timeout=120)
        return {"pdf": (out / "drawing.pdf").exists()}

    def compare_cad():
        from compare import compare, section_image
        cmp = compare(out / "machining", case)
        section_image(cmp, out / "compare_section.png")
        public = {k: v for k, v in cmp.items() if not k.startswith("_")}
        (out / "compare.json").write_text(json.dumps(public, indent=1))
        return {"section_rim_median_mm": public["section_rim"].get("median_mm"),
                "dim_errors": {k: v["error"] for k, v in public["dimensions"].items()}}

    if step("reconstruct", reconstruct) and not a.preview_only and step("machining", machining):
        step("package", package)
        step("drawing", drawing)
        if case and (case / "truth.json").exists():
            step("compare", compare_cad)
    chain = {"case": f"{case.parent.name}/{case.name}" if case else None, "text": a.text or (snapshot.get("source_text") if snapshot else None),
             "spec": spec, "hole_form": order.get("hole_form"),
             "parent": snapshot.get("parent") if snapshot else None,
             "recipe_sha256": snapshot.get("recipe_sha256") if snapshot else None,
             "preview_only": a.preview_only,
             "seconds": round(time.time() - t0, 1), "steps": steps, "manufacturing_status": "not_released"}
    from wheelcam.workbench_revision import digest
    import hashlib
    recipe_path = out / "reconstruct/recipe.json"
    if recipe_path.exists():
        chain["recipe_sha256"] = digest(json.loads(recipe_path.read_text()))
    (out / "chain.json").write_text(json.dumps(chain, ensure_ascii=False, indent=1))
    manifest = {"source_recipe_sha256": chain["recipe_sha256"], "parent": chain["parent"],
                "manufacturing_status": "not_released", "preview_only": a.preview_only,
                "complete": not a.preview_only and len(steps) >= 4 and all(s["ok"] for s in steps),
                "geometry_scope": "GLB retains style; machining STEP is a derived machining preparation with adjustments, not full style CAD",
                "artifacts": {str(p.relative_to(out)): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                                        "bytes": p.stat().st_size}
                              for p in sorted(out.rglob("*")) if p.is_file()}}
    (out / "delivery_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    from wheelcam.delivery_report import write as write_report
    report = write_report(out)                   # REPORT.md: the run's own files, summarized for the reviewer
    print(f"chain: {sum(s['ok'] for s in steps)}/{len(steps)} steps ok in {chain['seconds']} s -> {out}")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
