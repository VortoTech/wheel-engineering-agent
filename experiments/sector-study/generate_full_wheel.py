"""Generate a reproducible sixfold v10 visual experiment from checked-in evidence."""
from __future__ import annotations

import json
from pathlib import Path

from wheelcam.sector_surface import SurfaceControls, build_surface, to_full_wheel_glb, to_glb


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "experiments" / "sector-study"
OUTPUT = ROOT / "artifacts" / "sector-surface-v10"


def main():
    evidence = json.loads((EVIDENCE / "annotations.json").read_text())
    holdout = json.loads((EVIDENCE / "holdout.json").read_text())
    result = build_surface(evidence, SurfaceControls(surface_model="boundary-patches-v10"), holdout)
    local = to_glb(result)
    full = to_full_wheel_glb(result)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "master-sector.glb").write_bytes(local)
    (OUTPUT / "full-wheel-preview.glb").write_bytes(full)
    report = {key: value for key, value in result.items() if key not in {"positions", "triangles", "boundary_xyz", "boundary"}}
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({
        "local_bytes": len(local), "full_bytes": len(full),
        "groups": report["full_wheel_preview"]["groups"],
        "connected_or_fused": report["full_wheel_preview"]["connected_or_fused"],
        "integrity": report["integrity"], "gate": report["gate"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
