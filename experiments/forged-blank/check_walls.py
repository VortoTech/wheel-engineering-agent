"""Probe spoke side walls: vertical rays 2.5 mm inside each spoke edge must pass through solid
material from top to back: exactly 2 surface hits, at least 70% of the full web depth apart.
Catches back pockets cutting away a wall (the ray then sees only the thin top skin).

Run: PYTHONPATH=services .venv/bin/python experiments/forged-blank/check_walls.py <recipe.json> <build dir>
"""
import math
import sys
from dataclasses import replace
from pathlib import Path

import cadquery as cq
import numpy as np
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

sys.path.insert(0, str(Path(__file__).parent))
import build as B  # noqa: E402


def main(recipe, build_dir):
    overrides, _ = B.load_recipe(Path(recipe))
    p = replace(B.ForgedWheel(), **overrides)
    part = cq.importers.importStep(str(Path(build_dir) / 'part.step')).val()
    inter = BRepIntCurveSurface_Inter()
    bad, probes = [], 0
    for path, _, knots in B.spoke_geometry(p)[1]:
        ts, ws = zip(*knots)
        pts = np.array(path)
        cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
        at = lambda u: np.array([np.interp(u * cum[-1], cum, pts[:, 0]), np.interp(u * cum[-1], cum, pts[:, 1])])
        for t in np.linspace(.2, .8, 13):
            edge = float(np.interp(t, ts, ws)) / 2
            tx, ty = at(t + .02) - at(t - .02)
            length = math.hypot(tx, ty)
            nx, ny = -ty / length, tx / length
            cx, cy = at(t)
            for s in (edge - 2.5, 2.5 - edge):
                inter.Init(part.wrapped, gp_Lin(gp_Pnt(cx + nx * s, cy + ny * s, 200), gp_Dir(0, 0, -1)), 1e-6)
                zs = set()
                while inter.More():
                    zs.add(round(inter.Pnt().Z(), 3))
                    inter.Next()
                probes += 1
                x, y = cx + nx * s, cy + ny * s
                web = B.z_top(p, math.hypot(x, y)) - B.z_back(p, math.hypot(x, y))
                zs = sorted(zs, reverse=True)
                if len(zs) != 2 or zs[0] - zs[1] < .7 * web:
                    bad.append((round(float(t), 2), round(s, 1), zs[:4], round(web, 1)))
    print(probes, 'wall probes;', len(bad), 'not solid top-to-back', bad[:5])
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:3]))
