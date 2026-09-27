"""The whole demo chain for one scrubbed real order, in one command.

    PYTHONPATH=services .venv/bin/python scripts/demo_chain.py runs/real-orders/case-03/d20w10.5 --out runs/demo/m59

1 reconstruct   wheel_skill on the order render + confirmed specs (mesh kernel, seconds)
2 package       drawing, process plan, reference NC and cutting simulation (scripts/build_manufacturing_demo.py)
3 machining     machining-level STEP with the order's hole form (wheelcam.machining_step)
4 compare       the machining STEP's section and dimensions against the factory CAD (truth.json)
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
                kernel="mesh", spec_evidence={k: {"source": "drawing"} for k in spec})
        return {"readiness": r["readiness"], "spokes": r["parameters"].get("spokes", {}).get("value"),
                "checks_failed": [k for k, v in r["checks"].items() if not v.get("pass", True)]}

    def package():
        subprocess.run([sys.executable, str(ROOT / "scripts/build_manufacturing_demo.py"),
                        "--recipe", str(out / "reconstruct/recipe.json"), "--spec", str(case / "spec.json"),
                        "--out", str(out / "package"), "--drawing-no", case.parent.name.upper()],
                       check=True, capture_output=True, text=True)
        plan = json.loads((out / "package/process_plan.json").read_text())
        return {"simulation": (plan.get("simulation_3d") or {}).get("status")}

    def machining():
        from wheelcam.machining_step import export
        r = export(json.loads((out / "reconstruct/recipe.json").read_text()), out / "machining",
                   order.get("hole_form"), spec.get("et_mm"))
        return {"checks_failed": [k for k, c in r["checks"].items() if c["pass"] is False],
                "adjustments": r["adjustments"]}

    def compare_cad():
        from compare import compare, section_image
        cmp = compare(out / "machining", case)
        section_image(cmp, out / "compare_section.png")
        public = {k: v for k, v in cmp.items() if not k.startswith("_")}
        (out / "compare.json").write_text(json.dumps(public, indent=1))
        return {"section_rim_median_mm": public["section_rim"].get("median_mm"),
                "dim_errors": {k: v["error"] for k, v in public["dimensions"].items()}}

    if step("reconstruct", reconstruct):
        step("package", package)
        step("machining", machining)
        if (case / "truth.json").exists() and (out / "machining/recipe.json").exists():
            step("compare", compare_cad)
    chain = {"case": f"{case.parent.name}/{case.name}", "spec": spec, "hole_form": order.get("hole_form"),
             "seconds": round(time.time() - t0, 1), "steps": steps, "manufacturing_status": "not_released"}
    (out / "chain.json").write_text(json.dumps(chain, ensure_ascii=False, indent=1))
    print(f"chain: {sum(s['ok'] for s in steps)}/{len(steps)} steps ok in {chain['seconds']} s -> {out}")


if __name__ == "__main__":
    main()
