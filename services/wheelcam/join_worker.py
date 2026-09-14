"""Window-edge rounding and the rim ↔ centre join with junction fillets, run in a child process.

OCCT fillet builds cannot be interrupted from Python (Message_ProgressIndicator is not subclassable
in OCP): one join attempt on photo #18 ran over 20 minutes and a window-edge fillet on photo #20 over
12 (2026-09-13). The child gets both bodies as native BREP (lossless) and is killed on timeout.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cadquery as cq


def join_with_timeout(rim, centre, params, timeout_s):
    """(shape, applied, rounding) from the child, or None when it failed, timed out or returned an
    unsound body. params: radius, split, attempts, and optionally round = {top, bottom, hub_radius, fillet}."""
    with tempfile.TemporaryDirectory(prefix="wheelcam-join-") as folder:
        folder = Path(folder)
        rim.exportBrep(str(folder / "rim.brep"))
        centre.exportBrep(str(folder / "centre.brep"))
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        try:
            done = subprocess.run([sys.executable, "-m", "wheelcam.join_worker", str(folder), json.dumps(params)],
                                  env=env, timeout=timeout_s, capture_output=True)
        except subprocess.TimeoutExpired:   # run() kills the child before raising
            return None
        if done.returncode != 0 or not (folder / "joined.brep").exists():
            return None
        shape = cq.Shape.importBrep(str(folder / "joined.brep"))
        if not shape.isValid() or len(shape.Solids()) != 1:
            return None
        result = json.loads((folder / "result.json").read_text())
        return shape, result["applied"], result["rounding"]


if __name__ == "__main__":
    from .geometry import _fuse_with_fillet, round_window_edges
    folder, params = Path(sys.argv[1]), json.loads(sys.argv[2])
    rim = cq.Shape.importBrep(str(folder / "rim.brep"))
    centre = cq.Shape.importBrep(str(folder / "centre.brep"))
    rounding = {}
    if params.get("round"):
        r = params["round"]
        centre, rounding = round_window_edges(centre, r["top"], r["bottom"], r["hub_radius"], r["fillet"])
    shape, applied = _fuse_with_fillet([rim, centre], params["radius"], params["split"],
                                       attempts=[tuple(a) for a in params["attempts"]])
    shape.exportBrep(str(folder / "joined.brep"))
    (folder / "result.json").write_text(json.dumps({"applied": applied, "rounding": rounding}))
