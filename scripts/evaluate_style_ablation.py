"""Frozen, same-input preset vs style-agent comparison; development-set evidence only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import time
from dataclasses import asdict
from pathlib import Path

LOCKED = ("lip_r", "width", "pcd", "bolts", "center_bore_r", "bolt_d", "seat_d", "seat_cone_deg", "spokes", "outlines", "web_thick_hub")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compare(before, after, before_checks, after_checks, before_score, after_score):
    changed = [key for key in LOCKED if before.get(key) != after.get(key)]
    failed = {name: [key for key, check in checks.items() if check.get("pass") is not True]
              for name, checks in (("baseline", before_checks), ("candidate", after_checks))}
    gain = before_score["edge_mm"] - after_score["edge_mm"]
    return {"locked_fields_unchanged": not changed, "changed_locked_fields": changed,
            "checks_failed": failed, "valid_pair": not changed and not any(failed.values()) and bool(before_checks) and bool(after_checks),
            "edge_reduction_mm": round(gain, 3),
            "edge_reduction_percent": round(gain / before_score["edge_mm"] * 100, 2) if before_score["edge_mm"] else None,
            "window_iou_delta": round(after_score["window_iou"] - before_score["window_iou"], 3)}


def evaluate(case, out):
    from wheelcam import mesh_build, style_agent
    from wheelcam.forged_blank import recipe_from_dict
    from wheelcam.wheel_skill import run
    case, out = Path(case), Path(out)
    out.mkdir(parents=True)
    order = json.loads((case / "spec.json").read_text())
    spec = order["spec"]
    started = time.monotonic()
    report = run(case / "front.jpg", spec, out / "baseline",
                 oblique=case / "oblique.jpg" if (case / "oblique.jpg").exists() else None,
                 kernel="mesh", hole_form=order.get("hole_form"),
                 spec_evidence={k: {"source": "drawing"} for k in spec})
    baseline_seconds = time.monotonic() - started
    recipe = json.loads((out / "baseline/recipe.json").read_text())
    started = time.monotonic()
    corrected, log = style_agent.run(recipe, case / "front.jpg", out / "candidate", spec)
    agent_seconds = time.monotonic() - started
    # Re-score the FINAL accepted recipe, including all accepted pocket edits. The search score
    # alone evaluates only flank candidates and cannot stand in for this final measurement.
    started = time.monotonic()
    _, photo, centre, radius = style_agent.wheel_crop(case / "front.jpg")
    final_score = style_agent.edge_score(corrected, photo, centre, radius)
    body, _ = mesh_build.build(corrected)
    final_checks = mesh_build.verify(body, recipe_from_dict(corrected), spec)
    search = next(step for step in log["steps"] if step["step"] == "search")
    base_score = {"edge_mm": search["scores"][0]["edge_mm"], "window_iou": search["window_iou"]}
    normalize = lambda r: json.loads(json.dumps(asdict(recipe_from_dict(r))))
    result = {"baseline": {"scores": base_score, "checks": report["checks"], "build_seconds": round(baseline_seconds, 2)},
              "candidate": {"scores": final_score, "checks": final_checks, "agent_seconds": round(agent_seconds, 2)},
              "final_scoring_seconds": round(time.monotonic() - started, 2),
              "comparison": compare(normalize(recipe), normalize(corrected), report["checks"], final_checks, base_score, final_score),
              "changed": log["changed"], "readiness": "L0", "manufacturing_status": "not_released"}
    (out / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("output must be new or empty")
    args.out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    sources = sorted((root / "services/wheelcam").glob("*.py")) + [Path(__file__)]
    rows = [{"case": f"{case.parent.name}-{case.name}",
             "inputs": {name: sha(case / name) for name in ("front.jpg", "oblique.jpg", "spec.json") if (case / name).exists()}}
            for case in args.cases]
    manifest = {"revision": args.revision, "platform": platform.platform(), "machine": platform.machine(),
                "model": os.getenv("WHEELCAM_VLM_MODEL"), "cases": rows,
                "code_sha256": {str(p.relative_to(root)): sha(p) for p in sources},
                "scope": "Frozen existing development cases; same-photo fitting objective, not holdout accuracy or full Skill ablation.",
                "selection": "case-03 M59 main demo and case-01 contrasting design; selected before this run",
                "treatment": "same baseline recipe, input, mesh kernel and score; candidate enables existing style agent; no retuning"}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    results = []
    for case, row in zip(args.cases, rows):
        t0 = time.monotonic()
        try:
            result = evaluate(case, args.out / row["case"])
            results.append({"case": row["case"], "completed": True, **result})
        except Exception as exc:
            results.append({"case": row["case"], "completed": False, "error": f"{type(exc).__name__}: {exc}"[:500]})
        results[-1]["total_seconds"] = round(time.monotonic() - t0, 2)
        (args.out / "summary.json").write_text(json.dumps({"attempts": len(rows), "results": results}, ensure_ascii=False, indent=2))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
