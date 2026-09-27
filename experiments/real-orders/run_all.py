"""Run the wheel skill on every scrubbed real order and compare each result with its factory CAD.

    .venv/bin/python experiments/real-orders/run_all.py [--cases runs/real-orders] [--out runs/real-orders-eval]
        [--kernel mesh|brep] [--only case-03] [--jobs 2]

Each case size runs in its own process under scripts/capped.sh (CAP_KB, default 20 GB). The specs
come from the order sheet (spec.json, evidence "drawing"); front and oblique renders are the input.
Per size: OUT/<kernel>/<case>-<size>/ with the skill's outputs, compare.json and compare_section.png.
OUT/<kernel>/summary.json and the printed table give the build success rate (STEP written and read
back for brep) and the errors against the CAD.
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


def one(size_dir: Path, out: Path, kernel: str) -> dict:
    from wheelcam.wheel_skill import run
    sys.path.insert(0, str(HERE))
    from compare import compare, section_image
    spec = json.loads((size_dir / "spec.json").read_text())["spec"]
    evidence = {k: {"source": "drawing"} for k in spec}
    build = out / "build"
    t = time.time()
    oblique = size_dir / "oblique.jpg"
    result = run(size_dir / "front.jpg", spec, build, oblique if oblique.exists() else None, kernel=kernel,
                 spec_evidence=evidence)
    row = {"seconds": round(time.time() - t), "readiness": result["readiness"],
           "checks_failed": [k for k, v in result["checks"].items() if not v.get("pass", True)],
           "spokes": result["parameters"].get("spokes", {}).get("value"),
           "step": (build / "cad" / "wheel.step").exists()}
    cmp = compare(build, size_dir)
    section_image(cmp, out / "compare_section.png")
    public = {k: v for k, v in cmp.items() if not k.startswith("_")}
    (out / "compare.json").write_text(json.dumps(public, indent=1))
    row.update(section_rim_median_mm=public["section_rim"].get("median_mm"),
               section_hub_median_mm=public["section_hub"].get("median_mm"),
               dim_errors={k: v["error"] for k, v in public["dimensions"].items()})
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="runs/real-orders")
    ap.add_argument("--out", default="runs/real-orders-eval")
    ap.add_argument("--kernel", choices=("mesh", "brep"), default="mesh")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--one", help=argparse.SUPPRESS)
    a = ap.parse_args()
    cases, out = Path(a.cases), Path(a.out) / a.kernel
    if a.one:
        size_dir = cases / a.one
        target = out / a.one.replace("/", "-")
        row = one(size_dir, target, a.kernel)
        (target / "row.json").write_text(json.dumps(row, ensure_ascii=False, indent=1))
        return
    sizes = sorted(f"{p.parent.parent.name}/{p.parent.name}" for p in cases.glob("case-*/*/spec.json"))
    if a.only:
        sizes = [s for s in sizes if any(s.startswith(o) for o in a.only.split(","))]
    # HN191 and HN192 share one CAD; both are kept, they differ in finish
    out.mkdir(parents=True, exist_ok=True)

    def launch(size):
        target = out / size.replace("/", "-")
        target.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CAP_KB": os.environ.get("CAP_KB", "20000000"), "PYTHONPATH": str(ROOT / "services")}
        t = time.time()
        with open(target / "log.txt", "w") as log:
            code = subprocess.call([str(ROOT / "scripts/capped.sh"), sys.executable, __file__, "--cases", str(cases),
                                    "--out", a.out, "--kernel", a.kernel, "--one", size],
                                   stdout=log, stderr=subprocess.STDOUT, env=env)
        row_file = target / "row.json"
        if code == 0 and row_file.exists():
            row = {"size": size, **json.loads(row_file.read_text())}
        else:
            tail = (target / "log.txt").read_text(errors="ignore").strip().splitlines()[-3:]
            row = {"size": size, "error": " | ".join(tail)[-300:], "seconds": round(time.time() - t)}
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row

    with ThreadPoolExecutor(a.jobs) as pool:
        rows = list(pool.map(launch, sizes))
    ok = [r for r in rows if "error" not in r and (a.kernel == "mesh" or r.get("step"))]
    summary = {"kernel": a.kernel, "sizes": len(rows), "built": len(ok), "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"{a.kernel}: {len(ok)}/{len(rows)} built" + (" with STEP" if a.kernel == "brep" else ""))


if __name__ == "__main__":
    main()
