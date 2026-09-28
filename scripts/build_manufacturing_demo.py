"""Build a review-only drawing, process package and sampled simulation from a real-order recipe.

Example:
  PYTHONPATH=services .venv/bin/python scripts/build_manufacturing_demo.py \
    --recipe runs/m59-v1/recipe.json \
    --spec runs/real-orders/case-03/d20w10.5/spec.json \
    --out runs/m59-manufacturing-demo
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from wheelcam.drawing import drawing_svg
from wheelcam.forged_blank import recipe_from_dict
from wheelcam.manufacturing_demo import confirmed_recipe, create_package
from wheelcam.mesh_build import build, export_glb, verify


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--drawing-no", default="M59-REVIEW")
    args = ap.parse_args()
    source_recipe = json.loads(args.recipe.read_text())
    order_spec = json.loads(args.spec.read_text())
    order_path = args.spec.parent.parent / "order.json"
    order_variant = {}
    if order_path.exists():
        order = json.loads(order_path.read_text())
        for variant in order.get("variants", []):
            if (variant.get("diameter_in") == order_spec["spec"]["diameter_in"] and
                    variant.get("width_in") == order_spec["spec"]["width_in"]):
                order_variant = variant
                break
    corrected = confirmed_recipe(source_recipe, order_spec)
    p = recipe_from_dict(corrected)
    body, mesh_report = build(p)
    checks = verify(body, p, order_spec["spec"])
    if any(not result["pass"] for result in checks.values()):
        raise RuntimeError(f"Corrected demonstration mesh failed checks: {checks}")
    plan = create_package(source_recipe, order_spec, args.out, blank_code=order_variant.get("blank"),
                          design_body=body)
    export_glb(body, args.out / "wheel.glb")
    svg = drawing_svg(body, p, spec=order_spec["spec"], order=order_variant, drawing_no=args.drawing_no,
                      source="Order render + confirmed dimensions; reconstructed geometry")
    (args.out / "drawing.svg").write_text(svg)
    (args.out / "drawing.html").write_text(
        '<!doctype html><meta charset="utf-8"><style>'
        '@page{size:420mm 297mm;margin:0}html,body{margin:0;padding:0;width:420mm;height:297mm}'
        'img{display:block;width:420mm;height:297mm}</style>'
        '<img src="drawing.svg" alt="M59 review drawing">\n')
    summary = {
        "status": "not_released", "readiness": "L0", "drawing": "drawing.svg",
        "manufacturing_package": "process_plan.json", "simulation": "simulation.png",
        "checks": checks, "mesh_report": mesh_report,
        "source_recipe_sha256": hashlib.sha256(args.recipe.read_bytes()).hexdigest(),
        "source_spec_sha256": hashlib.sha256(args.spec.read_bytes()).hexdigest(),
        "note": "Corrected hole form is a local demonstration fork; it does not change the Spark STEP baseline.",
    }
    (args.out / "demo_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (args.out / "README.md").write_text(
        "# M59 engineering review package\n\n"
        "Status: **NOT RELEASED**. This is a local demonstration fork of the supplied recipe. "
        "The confirmed 15×32×60 bolt-hole form has been applied without changing the Spark baseline.\n\n"
        "- `drawing.svg` and `drawing.html`: A3 front and half-section views; print the HTML to A3 landscape PDF.\n"
        "- `wheel.glb`: corrected mesh preview; `demo_report.json`: geometry checks and source hashes.\n"
        "- `tool_list.csv` and `process_plan.json`: proposed turning, drilling, seat and window operations.\n"
        "- `reference.nc`: complete sampled G-code-style motion *comments*, not executable NC.\n"
        "- `simulation.png`: sampled 2.5D XY cutter sweep against window target.\n"
        "- `simulation_3d.png`, `stock_assumed.glb` and `stock_after_roughing.glb`: a 3D Boolean on the model's assumed "
        "revolved blank; see `process_plan.json` for gouge and remaining volume.\n"
        f"- Simulation conclusion: {plan['simulation_3d']['conclusion']}\n\n"
        "Factory CAD/STEP comparison, actual blank geometry, tooling, workholding, feeds, speeds, "
        "postprocessing and engineer approval are still required.\n")
    print(json.dumps({"output": str(args.out), "status": summary["status"],
                      "simulation": plan["simulation"], "mesh_status": mesh_report["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
