"""Multi-wheel eval: run the wheel skill on every wheel of evalset.json, score each build against its photos.

    .venv/bin/python experiments/forged-blank/evalset.py --photos DIR [--out runs/evalset] [--only hf-1,hf-3] [--jobs 2]
        [--kernel mesh|brep]

DIR/<id>/front.jpg and DIR/<id>/oblique.jpg are the official photos (not in the repo). Each wheel runs in
its own process under scripts/capped.sh (CAP_KB, default 20 GB); a failure is recorded, not fatal.
Writes OUT/summary.json and prints one line per wheel. A mesh build takes about a minute, a B-Rep
build 10-90 min (and failed on most wheels, 2026-09-26).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "services"))


def one(photos: Path, out: Path, wheel: dict, kernel: str = "mesh") -> dict:
    """Build and score one wheel in this process (called through --one)."""
    import cadquery as cq
    import numpy as np
    from PIL import Image
    from wheelcam.forged_blank import recipe_from_dict
    from wheelcam.forged_photo import fit_oblique_camera
    from wheelcam.visual_check import compare_front, compare_oblique
    from wheelcam.wheel_skill import _front_rim_hub, _oblique_rim_hub, run

    front, oblique = photos / wheel["id"] / "front.jpg", photos / wheel["id"] / "oblique.jpg"
    t = time.time()
    build_dir = out / "build"                      # the skill wants an empty directory of its own
    result = run(front, wheel["spec"], build_dir, oblique if oblique.exists() else None, kernel=kernel)
    row = {"id": wheel["id"], "seconds": round(time.time() - t), "readiness": result["readiness"],
           "mass_kg": result["mass_kg_6061"], "questions": len(result["questions"]),
           "checks_failed": [k for k, v in result["checks"].items() if not v.get("pass", True)],
           "spokes": result["parameters"].get("spokes", {}).get("value"),
           "planform": result["parameters"].get("planform", {}).get("value")}
    p = recipe_from_dict(json.loads((build_dir / "recipe.json").read_text()))
    if kernel == "mesh":
        from wheelcam.mesh_build import build
        shape = build(p)[0]
    else:
        shape = cq.importers.importStep(str(build_dir / "cad" / "wheel.step")).val()
    row["kernel"] = kernel
    load = lambda path: np.asarray(Image.open(path).convert("RGB"), float) / 255
    image = load(front)
    rim, _ = _front_rim_hub(image)
    centre = np.mean(rim, axis=0)
    radius = float(np.mean(np.linalg.norm(np.asarray(rim) - centre, axis=1)))
    fs, fov = compare_front(shape, image, tuple(centre), radius, p)
    Image.fromarray(fov).save(out / "check_front.png")
    row.update(front_window_iou=fs["window_iou"], front_edge_mm=fs["edge_mm"])
    if oblique.exists():
        image = load(oblique)
        o_rim, o_hub = _oblique_rim_hub(image)
        o_rim, o_hub, _ = fit_oblique_camera(image, p, o_rim, o_hub)
        os_, oov = compare_oblique(shape, image, o_rim, o_hub, p)
        Image.fromarray(oov).save(out / "check_oblique.png")
        row.update(oblique_window_iou=os_["window_iou"], oblique_silhouette_iou=os_["silhouette_iou"])
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", required=True)
    ap.add_argument("--out", default="runs/evalset")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--kernel", choices=("mesh", "brep"), default="mesh")
    ap.add_argument("--one", help=argparse.SUPPRESS)
    a = ap.parse_args()
    wheels = json.loads((HERE / "evalset.json").read_text())["wheels"]
    out = Path(a.out)
    if a.one:
        wheel = next(w for w in wheels if w["id"] == a.one)
        row = one(Path(a.photos), out / wheel["id"], wheel, a.kernel)
        (out / wheel["id"] / "eval_row.json").write_text(json.dumps(row, ensure_ascii=False, indent=1))
        return
    if a.only:
        wheels = [w for w in wheels if w["id"] in a.only.split(",")]
    out.mkdir(parents=True, exist_ok=True)

    def launch(wheel):
        wid = wheel["id"]
        (out / wid).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CAP_KB": os.environ.get("CAP_KB", "20000000"), "PYTHONPATH": str(ROOT / "services")}
        with open(out / wid / "log.txt", "w") as log:
            code = subprocess.call([str(ROOT / "scripts/capped.sh"), sys.executable, __file__, "--photos", a.photos,
                                    "--out", str(out), "--one", wid, "--kernel", a.kernel],
                                   stdout=log, stderr=subprocess.STDOUT, env=env)
        row_file = out / wid / "eval_row.json"
        if code == 0 and row_file.exists():
            row = json.loads(row_file.read_text())
        else:
            tail = (out / wid / "log.txt").read_text().strip().splitlines()[-3:]
            row = {"id": wid, "error": " | ".join(tail)[-300:]}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row

    with ThreadPoolExecutor(a.jobs) as pool:
        rows = list(pool.map(launch, wheels))
    (out / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    ok = sum("error" not in r for r in rows)
    print(f"{ok}/{len(rows)} built")


if __name__ == "__main__":
    main()
