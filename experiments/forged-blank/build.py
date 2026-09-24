"""Forged-blank experiment CLI: build a recipe, render comparison images, write a report.

The geometry lives in services/wheelcam/forged_blank.py (the forged-blank-v1 template); this script
adds matplotlib renders against the reference photo for eyeballing.

Run (needs services/ on the path): PYTHONPATH=services .venv/bin/python experiments/forged-blank/build.py
     ... --recipe experiments/forged-blank/recipes/<name>.json
"""
import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import cadquery as cq
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import numpy as np

from wheelcam.forged_blank import (ForgedWheel, build, recipe_from_dict, spoke_geometry,  # noqa: F401
                                   window_outlines, z_back, z_top)
from wheelcam.mass_properties import volume


def render(shape, ax, eye, title, extent):
    vertices, faces = shape.tessellate(.4, .15)
    tri = np.array([v.toTuple() for v in vertices])[np.array(faces)]
    eye = np.array(eye, dtype=float)
    eye /= np.linalg.norm(eye)
    right = np.cross([0., 1., 0.], eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    xy = np.stack([tri @ right, tri @ up], axis=-1)
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    normal *= np.sign(normal @ eye)[:, None]
    key = np.array([-.35, .45, .82 * np.sign(eye[2] or 1)]) / np.linalg.norm([-.35, .45, .82])  # light from the camera side
    half = (key + eye) / np.linalg.norm(key + eye)
    shade = .18 + .5 * np.clip(normal @ key, 0, 1) + .35 * np.clip(normal @ half, 0, 1) ** 24
    shade = np.clip(shade, 0, 1)
    order = np.argsort(tri.mean(1) @ eye)
    colors = np.stack([shade * .92, shade * .9, shade * .86], axis=1)
    ax.add_collection(PolyCollection(xy[order], facecolors=colors[order], edgecolors=colors[order], linewidths=.15))
    ax.set(xlim=(-extent, extent), ylim=(-extent, extent), aspect='equal')
    ax.set_title(title, fontsize=11)
    ax.axis('off')


def export(shape, output, name):
    path = output / f'{name}.step'
    cq.exporters.export(shape, str(path))
    loaded = cq.importers.importStep(str(path)).val()
    cq.Assembly(shape, color=cq.Color(.55, .53, .5)).export(str(output / f'{name}.glb'))
    return {'valid': loaded.isValid(), 'solids': len(loaded.Solids()),
            'volume_mm3': round(volume(shape), 1),
            'step_volume_relative_delta': abs(volume(loaded) - volume(shape)) / volume(shape)}


def main(output, recipe, photo, view_x):
    p = replace(ForgedWheel(), **recipe)
    output.mkdir(parents=True, exist_ok=True)
    stock, part, stages = build(p)
    exports = {'blank': export(stock, output, 'blank'), 'part': export(part, output, 'part')}
    extent = p.lip_r + 20
    fig = plt.figure(figsize=(20, 10), facecolor='white')
    ax = fig.add_subplot(1, 3, 1)
    if photo and photo.exists():
        ax.imshow(plt.imread(photo))
        ax.set_title('Reference photo', fontsize=11)
    ax.axis('off')
    render(part, fig.add_subplot(1, 3, 2), [view_x, .08, 1.], 'Prototype, photo-like view', extent)
    render(part, fig.add_subplot(1, 3, 3), [0, 0, 1.], 'Prototype, front', extent)
    fig.suptitle('Forged-blank prototype — dimensions are design assumptions, not measurements', fontsize=12)
    fig.tight_layout()
    fig.savefig(output / 'comparison.png', dpi=110)
    plt.close(fig)
    fig = plt.figure(figsize=(14, 7), facecolor='white')
    render(stock, fig.add_subplot(1, 2, 1), [view_x, .08, 1.], 'Forging blank', extent)
    render(part, fig.add_subplot(1, 2, 2), [.4, .2, -1.], 'Machined part, back', extent)
    fig.tight_layout()
    fig.savefig(output / 'blank-and-back.png', dpi=110)
    plt.close(fig)
    report = {'experiment': 'forged-blank-v1', 'dimensions_source': 'design_assumptions',
              'manufacturing_status': 'not_released', 'stages': stages, 'exports': exports,
              'removal_ratio': round(1 - volume(part) / volume(stock), 4),
              'part_mass_kg_6061': round(volume(part) * 2700 / 1e9, 2)}
    (output / 'recipe.json').write_text(json.dumps(asdict(p), indent=2))
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def load_recipe(path):
    """Recipe JSON: ForgedWheel field overrides plus optional _photo / _view_x render hints."""
    data = json.loads(path.read_text()) if path else {}
    hints = {k: data.pop(k) for k in list(data) if k.startswith('_')}
    try:
        validated = recipe_from_dict(data)       # same validation as the API
    except ValueError as exc:
        raise SystemExit(str(exc))
    return {k: getattr(validated, k) for k in data}, hints

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/forged-blank-v1'))
    parser.add_argument('--recipe', type=Path, help='JSON file overriding ForgedWheel fields')
    parser.add_argument('--photo', type=Path)
    args = parser.parse_args()
    recipe, hints = load_recipe(args.recipe)
    photo = args.photo or (Path(hints['_photo']).expanduser() if '_photo' in hints else None)
    main(args.output, recipe, photo, hints.get('_view_x', -.62))
