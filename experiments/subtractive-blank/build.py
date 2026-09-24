"""Reproducible virtual-blank subtraction experiment; all dimensions are assumptions.

Run: PYTHONPATH=services .venv/bin/python experiments/subtractive-blank/build.py
"""
import argparse
import json
import math
from pathlib import Path

import cadquery as cq
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import numpy as np


def polar(r, angle):
    a = math.radians(angle)
    return r * math.cos(a), r * math.sin(a)


def window(points):
    prism = cq.Workplane('XY', origin=(0, 0, -25)).polyline(points).close().extrude(70)
    return prism.edges('|Z').fillet(3).val()


def render(shape, ax, title, oblique=False):
    vertices, faces = shape.tessellate(.7, .2)
    triangles = np.array([v.toTuple() for v in vertices])[np.array(faces)]
    eye = np.array([.5, -.7, 1.] if oblique else [0., 0., 1.])
    eye /= np.linalg.norm(eye)
    right = np.cross(np.array([0., 1., 0.]), eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    p = np.stack([triangles @ right, triangles @ up], axis=-1)
    normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    intensity = .35 + .55 * np.maximum(0, normal @ np.array([-.3, -.4, .866]))
    order = np.argsort(triangles.mean(1) @ eye)
    colors = np.repeat(intensity[:, None], 3, axis=1)
    ax.add_collection(PolyCollection(p[order], facecolors=colors[order], edgecolors='none'))
    ax.set(xlim=(-255, 255), ylim=(-255, 255), aspect='equal', title=title)
    ax.axis('off')


def main(output, groups, groove_depth):
    output.mkdir(parents=True, exist_ok=True)
    # One revolved cross-section includes hub, face web and barrel.
    profile = [(33, -18), (220, -18), (220, -100), (235, -100),
               (235, 30), (220, 30), (220, 26), (80, 8), (33, 8)]
    blank = cq.Workplane('XZ').polyline(profile).close().revolve().val()
    pitch = 360 / groups
    large = window([polar(108, 22), polar(204, 12), polar(212, 20),
                    polar(212, pitch-20), polar(204, pitch-12), polar(108, pitch-22)])
    split = window([(137, -3.5), (205, -12), (213, -8), (213, 8), (205, 12), (137, 3.5)])
    roots = [cq.Workplane('XY', origin=(98, y, -25)).slot2D(26, 6, 0).extrude(70).val()
             for y in (-11, 11)]
    body = blank
    stages = []
    for name, cutters in [('windows', [large, split]), ('root_slots', roots)]:
        before = body.Volume()
        for i in range(groups):
            for tool in cutters:
                body = body.cut(tool.rotate((0, 0, 0), (0, 0, 1), i*pitch))
        body = body.clean()
        stages.append({'feature': name, 'removed_mm3': before-body.Volume(),
                       'valid': body.isValid(), 'solids': len(body.Solids())})
    base = body
    # Tapered loft removes a shallow channel on each Y arm. Floor follows face slope.
    before = body.Volume()
    for sign in (-1, 1):
        wires = []
        for r, half_width in [(123, .8), (155, 2.8), (194, 2.0), (209, .8)]:
            y = sign * (9 + (r-123)*.12)
            z = 8 + (math.hypot(r, y)-80)*18/140
            points = [(r, y-half_width, z-groove_depth), (r, y+half_width, z-groove_depth),
                      (r, y+half_width, z+8), (r, y-half_width, z+8)]
            wires.append(cq.Wire.makePolygon([cq.Vector(*p) for p in points], close=True))
        cutter = cq.Solid.makeLoft(wires, ruled=True)
        for i in range(groups):
            body = body.cut(cutter.rotate((0, 0, 0), (0, 0, 1), i*pitch))
    body = body.clean()
    stages.append({'feature': 'tapered_grooves', 'removed_mm3': before-body.Volume(),
                   'valid': body.isValid(), 'solids': len(body.Solids())})
    if any(not s['valid'] or s['solids'] != 1 or s['removed_mm3'] <= 0 for s in stages):
        raise ValueError(f'Invalid subtraction: {stages}')
    reports = {}
    for name, shape in [('blank', blank), ('windows', base), ('candidate', body)]:
        path = output / f'{name}.step'
        cq.exporters.export(shape, str(path))
        loaded = cq.importers.importStep(str(path)).val()
        delta = abs(loaded.Volume()-shape.Volume())/shape.Volume()
        if not loaded.isValid() or len(loaded.Solids()) != 1 or delta > 5e-5:
            raise ValueError(f'STEP readback failed: {name}, {delta}')
        cq.Assembly(shape, color=cq.Color(.6, .6, .6)).export(str(output/f'{name}.glb'))
        reports[name] = {'valid': True, 'solid_count': 1, 'volume_mm3': shape.Volume(),
                         'step_volume_relative_delta': delta}
    excess = body.cut(blank).Volume()
    if excess > .001:
        raise ValueError('Candidate extends outside blank')
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), facecolor='white')
    for col, (name, shape) in enumerate([('Virtual blank', blank), ('Windows + root slots', base), ('Tapered shallow grooves', body)]):
        render(shape, axes[0, col], name)
        render(shape, axes[1, col], name, True)
    fig.tight_layout()
    fig.savefig(output/'comparison.png', dpi=150)
    plt.close(fig)
    report = {'experiment': 'subtractive-blank-v1', 'groups': groups, 'group_count_source': 'design_assumption',
              'groove_depth_mm': groove_depth, 'dimensions_source': 'design_assumptions',
              'manufacturing_status': 'not_released', 'photo_fidelity': 'not_validated',
              'outside_blank_mm3': excess, 'stages': stages, 'artifacts': reports,
              'limitations': ['Simplified rim profile', 'No bolt seats or back-face reconstruction',
                              'No fillet continuity or wall-thickness validation', 'Not an actual casting blank']}
    (output/'report.json').write_text(json.dumps(report, indent=2))
    (output/'recipe.json').write_text(json.dumps({'groups': groups, 'groove_depth_mm': groove_depth,
                                                'blank_profile_rz_mm': profile}, indent=2))
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/subtractive-blank-v1'))
    parser.add_argument('--groups', type=int, choices=(5, 6), default=5)
    parser.add_argument('--groove-depth', type=float, default=2)
    args = parser.parse_args()
    if not 0 < args.groove_depth <= 4:
        parser.error('groove depth must be in (0, 4] mm')
    main(args.output, args.groups, args.groove_depth)
