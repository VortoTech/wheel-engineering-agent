"""Machining-level STEP for every real order, from the recipes of a mesh eval, compared with the CAD.

    .venv/bin/python experiments/real-orders/run_machining.py [--recipes runs/real-orders-eval/mesh]
        [--cases runs/real-orders] [--out runs/real-orders-eval/machining] [--jobs 4]

Each size runs wheelcam.machining_step in its own process under scripts/capped.sh (CAP_KB, default
12 GB) with the order's hole form and ET. Writes OUT/<case>-<size>/ (machining.step, reports,
compare.json, compare_section.png) and OUT/summary.json; prints "machining: x/13 STEP passed".
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
sys.path.insert(0, str(HERE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipes", default="runs/real-orders-eval/mesh")
    ap.add_argument("--cases", default="runs/real-orders")
    ap.add_argument("--out", default="runs/real-orders-eval/machining")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    from compare import compare, section_image
    cases, out = Path(a.cases), Path(a.out)
    sizes = sorted(f"{p.parent.parent.name}/{p.parent.name}" for p in cases.glob("case-*/*/spec.json"))
    env = {**os.environ, "CAP_KB": os.environ.get("CAP_KB", "12000000"), "PYTHONPATH": str(ROOT / "services")}

    def one(size):
        name = size.replace("/", "-")
        target, recipe = out / name, Path(a.recipes) / name / "build" / "recipe.json"
        target.mkdir(parents=True, exist_ok=True)
        t = time.time()
        with open(target / "log.txt", "w") as log:
            code = subprocess.call([str(ROOT / "scripts/capped.sh"), sys.executable, "-m", "wheelcam.machining_step",
                                    str(recipe), "--out", str(target), "--spec", str(cases / size / "spec.json")],
                                   stdout=log, stderr=subprocess.STDOUT, env=env)
        report = target / "machining_report.json"
        if code != 0 or not report.exists():
            tail = (target / "log.txt").read_text(errors="ignore").strip().splitlines()[-3:]
            row = {"size": size, "error": " | ".join(tail)[-300:], "seconds": round(time.time() - t)}
        else:
            rep = json.loads(report.read_text())
            cmp = compare(target, cases / size)
            section_image(cmp, target / "compare_section.png")
            public = {k: v for k, v in cmp.items() if not k.startswith("_")}
            (target / "compare.json").write_text(json.dumps(public, indent=1))
            row = {"size": size, "seconds": rep["seconds"], "windows": rep["windows"], "faces": rep["face_count"],
                   "passed": all(c["pass"] is not False for c in rep["checks"].values()),
                   "checks_failed": [k for k, c in rep["checks"].items() if c["pass"] is False],
                   "section_rim_median_mm": public["section_rim"].get("median_mm"),
                   "dim_errors": {k: v["error"] for k, v in public["dimensions"].items()}}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row

    with ThreadPoolExecutor(a.jobs) as pool:
        rows = list(pool.map(one, sizes))
    ok = [r for r in rows if r.get("passed")]
    (out / "summary.json").write_text(json.dumps({"kernel": "machining_step", "sizes": len(rows), "passed": len(ok),
                                                  "rows": rows}, ensure_ascii=False, indent=1))
    print(f"machining: {len(ok)}/{len(rows)} STEP passed")


if __name__ == "__main__":
    main()
