"""The whole demo chain for one scrubbed real order, in one command.

    PYTHONPATH=services .venv/bin/python scripts/demo_chain.py runs/real-orders/case-03/d20w10.5 --out runs/demo/m59

1 reconstruct   wheel_skill on the order render + confirmed specs (mesh kernel, seconds)
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
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "experiments/real-orders"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path, help="runs/real-orders/case-XX/<size>")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    case, out = a.case, a.out
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; use a new directory")
    out.mkdir(parents=True, exist_ok=True)
    order = json.loads((case / "spec.json").read_text())
    import shutil
    (out / "reference").mkdir()
    for name in ("front.jpg", "oblique.jpg", "back.jpg"):        # the scrubbed order renders, for the workbench
        if (case / name).exists():
            shutil.copy(case / name, out / "reference" / name)
    spec = order["spec"]
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
        from wheelcam.wheel_skill import run
        oblique = case / "oblique.jpg"
        r = run(case / "front.jpg", spec, out / "reconstruct", oblique if oblique.exists() else None,
                kernel="mesh", spec_evidence={k: {"source": "drawing"} for k in spec}, hole_form=order.get("hole_form"))
        return {"readiness": r["readiness"], "spokes": r["parameters"].get("spokes", {}).get("value"),
                "checks_failed": [k for k, v in r["checks"].items() if not v.get("pass", True)]}

    def package():
        # the process plan, reference NC and simulation read the machining STEP pair (stock + part)
        done = subprocess.run([sys.executable, str(ROOT / "scripts/build_manufacturing_step_demo.py"),
                               "--machining-dir", str(out / "machining"), "--spec", str(case / "spec.json"),
                               "--out", str(out / "package")], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "package failed")
        plan = json.loads((out / "package/process_plan.json").read_text())
        sim = plan.get("simulation_3d") or plan.get("simulation") or {}
        return {"simulation": sim.get("status"), "conclusion": sim.get("conclusion")}

    def machining():
        from wheelcam.machining_step import export
        r = export(json.loads((out / "reconstruct/recipe.json").read_text()), out / "machining",
                   order.get("hole_form"), spec.get("et_mm"))
        return {"checks_failed": [k for k, c in r["checks"].items() if c["pass"] is False],
                "adjustments": r["adjustments"]}

    def drawing():
        from wheelcam.drawing import drawing_svg
        from wheelcam.forged_blank import recipe_from_dict
        from wheelcam.mesh_build import build
        recipe = json.loads((out / "machining/recipe.json").read_text())   # the order's hole form applied
        body, _ = build(recipe)
        svg = drawing_svg(body, recipe_from_dict(recipe), spec=spec, order={}, drawing_no=case.parent.name.upper(),
                          source="订单渲染图 + 确认单尺寸；重建几何")
        (out / "drawing.svg").write_text(svg)
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.exists():                      # A3 PDF where a Chrome is at hand (the Mac)
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

    if step("reconstruct", reconstruct) and step("machining", machining):
        step("package", package)
        step("drawing", drawing)
        if (case / "truth.json").exists():
            step("compare", compare_cad)
    chain = {"case": f"{case.parent.name}/{case.name}", "spec": spec, "hole_form": order.get("hole_form"),
             "seconds": round(time.time() - t0, 1), "steps": steps, "manufacturing_status": "not_released"}
    (out / "chain.json").write_text(json.dumps(chain, ensure_ascii=False, indent=1))
    print(f"chain: {sum(s['ok'] for s in steps)}/{len(steps)} steps ok in {chain['seconds']} s -> {out}")


if __name__ == "__main__":
    main()
