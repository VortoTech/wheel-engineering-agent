"""Compare a reconstruction with the factory CAD of the same order (truth.json from ingest.py).

    .venv/bin/python experiments/real-orders/compare.py RUN_DIR CASE_SIZE_DIR [--out RUN_DIR]

RUN_DIR holds recipe.json (and engineering_report.json) of a wheel_skill run; CASE_SIZE_DIR is
runs/real-orders/case-XX/<size>/ with truth.json. Writes compare.json and compare_section.png:
- key dimensions side by side (lip OD, overall width, ET, PCD, bolts, bolt hole, centre bore)
- the revolved section against the CAD's coaxial edges: distance of every edge point (r, z) to the
  reconstruction outline, front lip faces aligned; rim (r > 200 mm) and hub (r < 120 mm) separately
Spoke faces are B-splines the x_t reader does not read, so they are not compared here.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "services"))

from wheelcam.forged_blank import recipe_from_dict  # noqa: E402
from wheelcam.mesh_build import blank_profile  # noqa: E402


def _to_polyline(q, poly):
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
    return float(np.min(np.linalg.norm(a + ab * t[:, None] - q, axis=1)))


def stats(d):
    d = np.asarray(d)
    return {"points": int(len(d)), "median_mm": round(float(np.median(d)), 2),
            "p90_mm": round(float(np.percentile(d, 90)), 2), "max_mm": round(float(d.max()), 2)} if len(d) else {}


def compare(run: Path, case: Path) -> dict:
    p = recipe_from_dict(json.loads((run / "recipe.json").read_text()))
    truth = json.loads((case / "truth.json").read_text())
    ours = np.array(blank_profile(p))
    edges = np.array(truth["profile_rz"], float)
    edges[:, 1] -= edges[:, 1].max()                    # the front of the lip at z = 0, as in the recipe
    rim = [_to_polyline(q, ours) for q in edges[edges[:, 0] > 200]]
    hub = [_to_polyline(q, ours) for q in edges[edges[:, 0] < 120]]
    checks = {}
    report = run / "engineering_report.json"
    if report.exists():
        checks = json.loads(report.read_text()).get("checks", {})
    measured = lambda k: checks.get(k, {}).get("measured_mm", checks.get(k, {}).get("derived_mm"))
    rows = {
        "lip_od_mm": (measured("outer_diameter") or 2 * p.lip_r, truth.get("lip_od_mm")),
        "overall_width_mm": (measured("overall_width") or p.width, truth.get("overall_width_mm")),
        "et_mm": (measured("offset_et"), truth.get("et_mm")),
        "pcd_mm": (p.pcd, truth.get("pcd_mm")),
        "bolts": (p.bolts, truth.get("bolts")),
        "bolt_hole_d_mm": (p.bolt_d, truth.get("bolt_hole_d_mm")),
        "center_bore_mm": (2 * p.center_bore_r, truth.get("center_bore_mm")),
    }
    dims = {k: {"reconstruction": None if a is None else round(float(a), 2), "cad": b,
                "error": None if a is None or b is None else round(float(a) - float(b), 2)} for k, (a, b) in rows.items()}
    return {"dimensions": dims, "section_rim": stats(rim), "section_hub": stats(hub),
            "_ours": ours.tolist(), "_edges": edges.tolist()}


def section_image(result, path, scale=2.2):
    from PIL import Image, ImageDraw
    ours, edges = np.array(result["_ours"]), np.array(result["_edges"])
    im = Image.new("RGB", (int(300 * scale) + 40, int(330 * scale) + 40), "white")
    d = ImageDraw.Draw(im)
    f = lambda r, z: (20 + r * scale, 30 + (-z) * scale)
    d.line([f(*q) for q in np.vstack([ours, ours[:1]])], fill=(40, 90, 220), width=2)
    for r, z in edges:
        x, y = f(r, z)
        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(220, 40, 40))
    s = result["section_rim"]
    d.text((25, 6), f"blue: reconstruction  red: factory CAD edges   rim median {s.get('median_mm')} mm, p90 {s.get('p90_mm')} mm",
           fill="black")
    im.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("case")
    ap.add_argument("--out")
    a = ap.parse_args()
    run, out = Path(a.run), Path(a.out or a.run)
    result = compare(run, Path(a.case))
    section_image(result, out / "compare_section.png")
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    (out / "compare.json").write_text(json.dumps(public, indent=1))
    print(json.dumps(public, indent=1))


if __name__ == "__main__":
    main()
