"""Build M59 review artifacts from an already exported machining STEP pair.

PYTHONPATH=services .venv/bin/python scripts/build_manufacturing_step_demo.py \
  --machining-dir runs/real-orders-eval/machining/case-03-d20w10.5 \
  --spec runs/real-orders/case-03/d20w10.5/spec.json \
  --out runs/m59-manufacturing-step-demo
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wheelcam.drawing import drawing_svg
from wheelcam.forged_blank import recipe_from_dict
from wheelcam.manufacturing_step_demo import _step_mesh, create_step_package


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machining-dir", type=Path, required=True)
    ap.add_argument("--spec", type=Path)
    ap.add_argument("--forged-report", type=Path, help="完整造型 B-rep report.json；技术要求列出跳过工序")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    plan = create_step_package(args.machining_dir, args.out, spec_path=args.spec)
    p = recipe_from_dict(json.loads((args.machining_dir / "recipe.json").read_text()))
    machining_report = json.loads((args.machining_dir / "machining_report.json").read_text())
    forged_report = json.loads(args.forged_report.read_text()) if args.forged_report else {}
    _, part = _step_mesh(args.machining_dir / "machining.step")
    # drawing_svg uses the recipe frame [-width, 0], while STEP uses the midplane frame.
    part = part.translate([0, 0, -p.width / 2])
    spec = json.loads(args.spec.read_text())["spec"] if args.spec else {}
    drawing = drawing_svg(part, p, spec=spec, drawing_no=args.machining_dir.name,
                          source="加工级 machining.step；未发布", machining_report=machining_report,
                          forged_report=forged_report)
    (args.out / "drawing.svg").write_text(drawing)
    (args.out / "drawing.html").write_text(
        '<!doctype html><meta charset="utf-8"><style>'
        '@page{size:420mm 297mm;margin:0}html,body{margin:0;padding:0;width:420mm;height:297mm}'
        'img{display:block;width:420mm;height:297mm}</style>'
        '<img src="drawing.svg" alt="Machining STEP review drawing">\n')
    print(json.dumps({"output": str(args.out), "status": plan["status"],
                      "simulation": plan["simulation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
