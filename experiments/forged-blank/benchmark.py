"""HF6-4 benchmark: build the frozen recipe, score it against the official photos, compare with a baseline.

    .venv/bin/python experiments/forged-blank/benchmark.py --front FRONT.jpg --oblique ANGLE.jpg \
        [--recipe benchmarks/hf64.json] [--out runs/bench] [--baseline benchmarks/hf64.scores.json] [--write-baseline]

The official product photos are not in the repo (branded images, benchmark use only): pass their paths.
Exit 1 when a score is worse than the baseline by more than its tolerance. Run it under a memory cap
(a build once needed ~96 GB); it takes 10-15 min.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "services"))
TOLERANCE = {"front_window_iou": -0.02, "front_edge_mm": 0.4, "oblique_window_iou": -0.03,
             "oblique_silhouette_iou": -0.02, "oblique_edge_mm": 0.5}


def main():
    import cadquery as cq
    from wheelcam.forged_blank import export_model, recipe_from_dict
    from wheelcam.forged_photo import fit_oblique_camera
    from wheelcam.visual_check import compare_front, compare_oblique
    from wheelcam.wheel_skill import _front_rim_hub, _oblique_rim_hub

    ap = argparse.ArgumentParser()
    ap.add_argument("--front", required=True)
    ap.add_argument("--oblique", required=True)
    ap.add_argument("--recipe", default=str(HERE / "benchmarks/hf64.json"))
    ap.add_argument("--out", default="runs/bench-hf64")
    ap.add_argument("--baseline", default=str(HERE / "benchmarks/hf64.scores.json"))
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args()

    recipe = json.loads(Path(a.recipe).read_text())
    out = Path(a.out)
    t = time.time()
    report = export_model(recipe, out, {"forged": recipe})
    seconds = round(time.time() - t)
    p = recipe_from_dict(recipe)
    shape = cq.importers.importStep(str(out / "wheel.step")).val()
    front = np.asarray(Image.open(a.front).convert("RGB"), float) / 255
    oblique = np.asarray(Image.open(a.oblique).convert("RGB"), float) / 255
    rim, hub = _front_rim_hub(front)
    centre = np.mean(rim, axis=0)
    radius = float(np.mean(np.linalg.norm(np.asarray(rim) - centre, axis=1)))
    fs, fov = compare_front(shape, front, tuple(centre), radius, p)
    o_rim, o_hub = _oblique_rim_hub(oblique)
    o_rim, o_hub, _ = fit_oblique_camera(oblique, p, o_rim, o_hub)
    os_, oov = compare_oblique(shape, oblique, o_rim, o_hub, p)
    Image.fromarray(fov).save(out / "check_front.png")
    Image.fromarray(oov).save(out / "check_oblique.png")
    scores = {"front_window_iou": fs["window_iou"], "front_edge_mm": fs["edge_mm"],
              "oblique_window_iou": os_["window_iou"], "oblique_silhouette_iou": os_["silhouette_iou"],
              "oblique_edge_mm": os_["edge_mm"]}
    forged = report.get("forged", report)
    result = {"scores": scores, "build_seconds": seconds, "mass_kg_6061": forged.get("part_mass_kg_6061")}
    (out / "benchmark.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    if a.write_baseline:
        Path(a.baseline).write_text(json.dumps(result, indent=1))
        return 0
    base = json.loads(Path(a.baseline).read_text())["scores"]
    worse = [k for k, tol in TOLERANCE.items()
             if (scores[k] - base[k] < tol if tol < 0 else scores[k] - base[k] > tol)]
    for k in TOLERANCE:
        print(f"{k:24s} {base[k]:8.3f} -> {scores[k]:8.3f}{'  WORSE' if k in worse else ''}")
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
