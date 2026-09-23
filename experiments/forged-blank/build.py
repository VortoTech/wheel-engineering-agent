"""Forged-blank prototype: build a wheel in machining order from one parameter set.

revolved forging blank -> face facets -> through windows (2D sketch) -> spoke grooves -> lip-face pockets -> lug holes and seats.
All dimensions are design assumptions.

Run: PYTHONPATH=services .venv/bin/python experiments/forged-blank/build.py
     ... --recipe experiments/forged-blank/recipes/<name>.json
"""
import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

import cadquery as cq
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import numpy as np


@dataclass(frozen=True)
class ForgedWheel:
    # Rim envelope (20 x 9 J reference: z=0 is the lip front, -Z goes inboard).
    lip_r: float = 272.0
    lip_face_r_in: float = 224.0
    barrel_outer_r: float = 250.0
    barrel_inner_r: float = 240.0
    width: float = 230.0
    # Face: hub plateau depth and concave spoke surface rising to the ring.
    center_bore_r: float = 53.05
    hub_r: float = 80.0
    hub_z: float = -80.0
    ring_r: float = 218.0
    ring_z: float = -14.0
    concavity_exp: float = 1.35
    web_thick_hub: float = 54.0
    web_thick_ring: float = 30.0
    # Spokes. family 'y_split': stem then two arms; 'single': one spoke hub to rim.
    family: str = 'y_split'
    spokes: int = 6
    window_r_in: float = 94.0
    window_r_out: float = 216.0
    stem_w_hub: float = 46.0
    stem_w_split: float = 34.0      # single family: spoke width at the rim
    split_r: float = 132.0
    arm_angle_deg: float = 13.5
    arm_w: float = 22.0
    arm_bow: float = 6.0
    window_fillet: float = 5.0
    facet_deg: float = 20.0         # 0 = flat spoke tops
    # Spoke grooves: lateral centre as a fraction of half width (0 = on the ridge).
    groove_offsets: tuple = (0.62,)
    groove_w: float = 4.0
    groove_depth: float = 3.0
    # Lip face pockets (0 = plain lip face).
    lip_pockets: int = 20
    lip_pocket_r: tuple = (228.0, 247.0)
    lip_rib_w: float = 7.0
    lip_pocket_depth: float = 24.0
    # Hub interface.
    bolts: int = 6
    pcd: float = 139.7
    bolt_d: float = 22.0
    seat_d: float = 40.0
    seat_depth: float = 22.0


def z_top(p, r):
    r = min(max(r, p.hub_r), p.ring_r)
    t = (r - p.hub_r) / (p.ring_r - p.hub_r)
    return p.hub_z + (p.ring_z - p.hub_z) * t ** p.concavity_exp


def z_back(p, r):
    t = (min(max(r, p.hub_r), p.ring_r) - p.hub_r) / (p.ring_r - p.hub_r)
    return z_top(p, r) - (p.web_thick_hub + (p.web_thick_ring - p.web_thick_hub) * t)


def polar(r, deg):
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


def blank(p):
    """Revolved forging blank: hub, concave face web, lip face ring and barrel."""
    rs = np.linspace(p.hub_r, p.ring_r, 9)
    front = [(r, z_top(p, r)) for r in rs]
    back = [(r, z_back(p, r)) for r in rs[::-1]]
    lip_back = -min(14.0, p.lip_r - p.barrel_outer_r)
    wp = (cq.Workplane('XZ').moveTo(p.center_bore_r, p.hub_z).lineTo(p.hub_r, p.hub_z)
          .spline(front[1:], includeCurrent=True)
          .lineTo(p.lip_face_r_in, 0).lineTo(p.lip_r, 0).lineTo(p.lip_r, lip_back)
          .lineTo(p.barrel_outer_r + 2, lip_back - 12)
          .lineTo(p.barrel_outer_r, lip_back - 26).lineTo(p.barrel_outer_r, -p.width + 25)
          .lineTo(p.lip_r - 2, -p.width + 12).lineTo(p.lip_r - 2, -p.width)
          .lineTo(p.barrel_inner_r, -p.width).lineTo(p.barrel_inner_r, z_back(p, p.ring_r) - 3)
          .lineTo(p.ring_r + 4, z_back(p, p.ring_r))
          .spline(back, includeCurrent=True)
          .lineTo(p.center_bore_r, z_back(p, p.hub_r)).close())
    return wp.revolve(360, (0, 0, 0), (0, 1, 0)).val()


def spoke_geometry(p):
    """2D footprint polygons and centre segments (a, b, width) of spoke 0 along +X."""
    hub_w, hub_in = p.stem_w_hub / 2, p.window_r_in - 30
    if p.family == 'single':
        end_r = p.window_r_out + 10
        half = p.stem_w_split / 2
        side = [(hub_in, hub_w + 6), (p.window_r_in, hub_w),
                (p.window_r_in + 25, hub_w + (half - hub_w) * .25), (end_r, half)]
        return [side + [(x, -y) for x, y in side[::-1]]], [((p.window_r_in, 0.0), (p.window_r_out, 0.0), p.stem_w_split)]
    split = (p.split_r, 0.0)
    stem_pts = [(hub_in, hub_w + 6), (p.window_r_in, hub_w),
                (p.window_r_in + 16, p.stem_w_split / 2 + 3), (p.split_r, p.stem_w_split / 2)]
    stem = stem_pts + [(x, -y) for x, y in stem_pts[::-1]]
    arms, segments = [], [((p.window_r_in, 0.0), split, p.stem_w_split)]
    for sign in (1, -1):
        end = polar(p.window_r_out + 10, sign * p.arm_angle_deg)
        dx, dy = end[0] - split[0], end[1] - split[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        centre = []
        for t in np.linspace(0, 1, 7):
            bow = p.arm_bow * math.sin(math.pi * t) * sign
            centre.append((split[0] - 12 * (1 - t) + dx * t + nx * bow,
                           split[1] + dy * t + ny * bow))
        half = p.arm_w / 2
        left = [(x + nx * half, y + ny * half) for x, y in centre]
        right = [(x - nx * half, y - ny * half) for x, y in centre]
        arms.append(left + right[::-1])
        segments.append((split, polar(p.window_r_out, sign * p.arm_angle_deg), p.arm_w))
    return [stem] + arms, segments


def windows(p):
    polys, _ = spoke_geometry(p)
    pitch = 360 / p.spokes
    sk = cq.Sketch().circle(p.window_r_out).circle(p.window_r_in, mode='s')
    for i in range(p.spokes):
        c, s = math.cos(math.radians(i * pitch)), math.sin(math.radians(i * pitch))
        for poly in polys:
            pts = [(x * c - y * s, x * s + y * c) for x, y in poly]
            sk = sk.polygon(pts + [pts[0]], mode='s')
    tools = []
    for face in sk._faces.Faces():
        face = round_corners(face, p.window_fillet)
        tools.append(cq.Solid.extrudeLinear(face.translate((0, 0, -p.width)), cq.Vector(0, 0, p.width + 20)))
    return cq.Compound.makeCompound(tools)


def round_corners(face, radius, min_turn_deg=25):
    """2D fillet only at real corners; sampled polylines have near-collinear vertices."""
    corners = []
    for v in face.Vertices():
        edges = [e for e in face.Edges() if any(v.toTuple() == w.toTuple() for w in e.Vertices())]
        if len(edges) != 2:
            continue
        tangents = []
        for e in edges:
            at_start = (e.startPoint() - cq.Vector(v.toTuple())).Length < 1e-6
            t = e.tangentAt(0 if at_start else 1)
            tangents.append(t if at_start else -t)
        turn = 180 - math.degrees(tangents[0].getAngle(tangents[1]))
        if turn > min_turn_deg:
            corners.append(v)
    for r in (radius, radius * .6, radius * .3):
        try:
            return face.fillet2D(r, corners)
        except Exception:
            continue
    return face


def face_height(p, x, y, s, fade):
    """Machined spoke top at lateral offset s from the ridge (facets included)."""
    tan = math.tan(math.radians(p.facet_deg))
    return z_top(p, math.hypot(x, y)) - abs(s) * tan * fade


def segment_loft(p, a, b, section):
    """Ruled loft of quads section(t, fade, point_at) along segment a->b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    wires = []
    for t in np.linspace(0, 1, 7):
        fade = min(1.0, t / .18, (1 - t) / .18)
        cx, cy = a[0] + dx * t, a[1] + dy * t
        pts = section(fade, lambda s: (cx + nx * s, cy + ny * s))
        wires.append(cq.Wire.makePolygon([cq.Vector(*q) for q in pts], close=True))
    return cq.Solid.makeLoft(wires, ruled=True)


def facet_cutters(p):
    """Two sloped cuts per segment leave a ridge; lift 0.5 above the face at the ends (no cut)."""
    if p.facet_deg <= 0:
        return []
    tan = math.tan(math.radians(p.facet_deg))
    cutters = []
    for a, b, width in spoke_geometry(p)[1]:
        for side in (1, -1):
            def section(fade, at, side=side, reach=width / 2 + 5):
                lift = .5 - .8 * fade
                low = []
                for s in (-1.5 * side, reach * side):
                    x, y = at(s)
                    low.append((x, y, z_top(p, math.hypot(x, y)) + lift - side * s * tan * fade))
                return low + [(x, y, z_top(p, math.hypot(x, y)) + 25) for x, y, _ in low[::-1]]
            cutters.append(segment_loft(p, a, b, section))
    return cutters


def groove_cutters(p):
    """Channels milled into the finished spoke top, parallel to the facet slope."""
    cutters = []
    for a, b, width in spoke_geometry(p)[1]:
        for offset in p.groove_offsets:
            for side in ((1, -1) if offset else (1,)):
                centre = side * offset * width / 2

                def section(fade, at, centre=centre):
                    low = []
                    for s in (centre - p.groove_w / 2, centre + p.groove_w / 2):
                        x, y = at(s)
                        low.append((x, y, face_height(p, x, y, s, fade) + .5 - (p.groove_depth + .5) * fade))
                    return low + [(x, y, z + 30) for x, y, z in low[::-1]]
                cutters.append(segment_loft(p, a, b, section))
    return cutters


def lip_pockets(p):
    if not p.lip_pockets:
        return []
    pitch = 360 / p.lip_pockets
    r0, r1 = p.lip_pocket_r
    h0 = math.degrees(p.lip_rib_w / 2 / r0)
    h1 = math.degrees(p.lip_rib_w / 2 / r1)
    outline = ([polar(r0, a) for a in np.linspace(h0, pitch - h0, 6)]
               + [polar(r1, a) for a in np.linspace(pitch - h1, h1, 6)])
    sk = cq.Sketch().polygon(outline + [outline[0]]).vertices().fillet(3)
    tool = cq.Workplane('XY', origin=(0, 0, -p.lip_pocket_depth)).placeSketch(sk).extrude(p.lip_pocket_depth + 5).val()
    return [tool.rotate((0, 0, 0), (0, 0, 1), i * pitch) for i in range(p.lip_pockets)]


def lug_tools(p):
    tools = []
    for i in range(p.bolts):
        x, y = polar(p.pcd / 2, 180 / p.spokes + i * 360 / p.bolts)
        tools.append(cq.Workplane('XY', origin=(x, y, -p.width)).circle(p.bolt_d / 2).extrude(p.width + 10).val())
        seat_z = p.hub_z - p.seat_depth
        tools.append(cq.Workplane('XY', origin=(x, y, seat_z)).circle(p.seat_d / 2).extrude(40).val())
    return tools


def build(p):
    body = blank(p)
    stock = body
    stages = []

    def apply(name, tools, rotate=False):
        nonlocal body
        if not tools:
            return
        start, before = time.time(), body.Volume()
        pitch = 360 / p.spokes
        for i in range(p.spokes if rotate else 1):
            for tool in tools:
                body = body.cut(tool.rotate((0, 0, 0), (0, 0, 1), i * pitch) if rotate else tool)
        body = body.clean()
        stages.append({'op': name, 'removed_mm3': round(before - body.Volume(), 1),
                       'valid': body.isValid(), 'solids': len(body.Solids()),
                       'seconds': round(time.time() - start, 1)})

    apply('face_facets', facet_cutters(p), rotate=True)
    apply('through_windows', [windows(p)])
    apply('spoke_grooves', groove_cutters(p), rotate=True)
    apply('lip_pockets', lip_pockets(p))
    apply('lug_holes_and_seats', lug_tools(p))
    return stock, body, stages


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
    key = np.array([-.35, .45, .82]) / np.linalg.norm([-.35, .45, .82])
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
            'volume_mm3': round(shape.Volume(), 1),
            'step_volume_relative_delta': abs(loaded.Volume() - shape.Volume()) / shape.Volume()}


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
              'removal_ratio': round(1 - part.Volume() / stock.Volume(), 4),
              'part_mass_kg_6061': round(part.Volume() * 2700 / 1e9, 2)}
    (output / 'recipe.json').write_text(json.dumps(asdict(p), indent=2))
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def load_recipe(path):
    """Recipe JSON: ForgedWheel field overrides plus optional _photo / _view_x render hints."""
    data = json.loads(path.read_text()) if path else {}
    hints = {k: data.pop(k) for k in list(data) if k.startswith('_')}
    known = {f.name for f in fields(ForgedWheel)}
    unknown = set(data) - known
    if unknown:
        raise SystemExit(f'Unknown recipe fields: {sorted(unknown)}')
    for key in ('lip_pocket_r', 'groove_offsets'):
        if key in data:
            data[key] = tuple(data[key])
    return data, hints


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('artifacts/forged-blank-v1'))
    parser.add_argument('--recipe', type=Path, help='JSON file overriding ForgedWheel fields')
    parser.add_argument('--photo', type=Path)
    args = parser.parse_args()
    recipe, hints = load_recipe(args.recipe)
    photo = args.photo or (Path(hints['_photo']).expanduser() if '_photo' in hints else None)
    main(args.output, recipe, photo, hints.get('_view_x', -.62))
