"""Isolated visual experiments; no production model or project is overwritten.

These controls are hand-authored hypotheses, NOT measured photo reconstruction.
Run with the WheelCAM Python to create CAD and shared Blender control sections.
"""
import json
import math
from pathlib import Path

import cadquery as cq
import numpy as np
from scipy.interpolate import CubicSpline

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/route-study'
OUT.mkdir(parents=True, exist_ok=True)


def sections():
    radii = np.array([48, 65, 95, 140, 185, 220])
    # Width and height are independent. This replaces a cut plate with a blade.
    width = CubicSpline(radii, [19, 18, 12, 9, 10, 15], bc_type='natural')
    height = CubicSpline(radii, [15, 14, 11, 9, 8, 8], bc_type='natural')
    depth = CubicSpline(radii, [0, 3, 17, 38, 51, 55], bc_type='natural')
    spread = CubicSpline(radii, [25, 24, 20, 15, 12, 11], bc_type='natural')
    branches = []
    for sign in [-1, 1]:
        branch = []
        for r in np.linspace(48, 220, 15):
            angle = math.radians(90 + sign * float(spread(r)))
            branch.append(dict(center=[r*math.cos(angle), r*math.sin(angle), float(depth(r))],
                               tangent=[-math.sin(angle), math.cos(angle), 0],
                               normal=[math.cos(angle), math.sin(angle), 0],
                               width=float(width(r)), height=float(height(r))))
        branches.append(branch)
    return branches


def main():
    branches = sections()
    (OUT / 'controls.json').write_text(json.dumps({'branches': branches,
        'groups': 5, 'source': 'hand-authored shape hypothesis',
        'engineering_approved': False}, indent=2))
    pieces = []
    for branch in branches:
        wires = []
        for s in branch:
            plane = cq.Plane(origin=s['center'], xDir=s['tangent'], normal=s['normal'])
            wires.append(cq.Workplane(plane).ellipse(s['width']/2, s['height']/2).val())
        blade = cq.Solid.makeLoft(wires, ruled=False)
        for group in range(5):
            pieces.append(blade.rotate((0,0,0),(0,0,1),group*72))
    for outer, inner, z, thickness in [(247,239,51,8),(222,215,51,8),(246,241,-135,190),(55,29,-14,17)]:
        pieces.append(cq.Workplane('XY', origin=(0,0,z)).circle(outer).circle(inner).extrude(thickness).val())
    for i in range(15):
        a = math.radians(i*24)
        bar = cq.Workplane('XY').box(24,6,7).translate((230,0,55)).val()
        pieces.append(bar.rotate((0,0,0),(0,0,1),math.degrees(a)))
    compound = cq.Compound.makeCompound(pieces)
    cq.exporters.export(compound,str(OUT/'cad-loft.step'))
    cq.exporters.export(compound,str(OUT/'cad-loft.stl'),tolerance=.15,angularTolerance=.1)
    # Export baseline in the same Z-up coordinate system for fair rendering.
    baseline = cq.importers.importStep(str(ROOT/'data/models/857056f66bfe4548ab2444668852b117/wheel.step'))
    cq.exporters.export(baseline,str(OUT/'baseline.stl'),tolerance=.15,angularTolerance=.1)
    (OUT/'cad-report.json').write_text(json.dumps({'valid':compound.isValid(),
        'solid_count':len(compound.Solids()),'watertight_assembly':False,
        'note':'Separate overlapping visual parts, not a fused manufacturing solid.'},indent=2))
    print(OUT, flush=True)


if __name__ == '__main__':
    main()
