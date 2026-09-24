"""Forged-blank wheel template: build a wheel in machining order from one parameter set.

revolved forging blank -> face facets -> through windows (2D sketch) -> window rim edge break
-> spoke grooves -> back weight pockets -> lip-face pockets -> lug holes and seats.

Prototype origin and design notes: experiments/forged-blank/README.md. All default dimensions are
design assumptions, not measurements; manufacturing status is always not_released.
"""
import math
import time
from dataclasses import asdict, dataclass, fields, replace

import cadquery as cq
import numpy as np

from .mass_properties import volume  # GK integration; plain Volume() is wrong on many-face bodies

TEMPLATE_VERSION = "forged-blank-v1"

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
    family: str = 'y_split'         # also 'single' and 'skeleton' (graph from `skeleton`)
    # skeleton family: {"nodes": {name: [r_mm, angle_deg]}, "edges": [[from, to, w_from, w_to], ...]}
    # for one spoke group; angles may pass ±pitch/2 so neighbouring groups can join into a mesh.
    skeleton: dict = None
    spokes: int = 6
    window_r_in: float = 94.0
    window_r_out: float = 216.0
    stem_w_hub: float = 46.0
    stem_w_split: float = 34.0      # single family: spoke width at the rim
    split_r: float = 132.0
    arm_angle_deg: float = 13.5
    arm_w: float = 22.0
    arm_bow: float = 6.0
    spoke_sweep_deg: float = 0.0    # spoke rotates progressively toward the rim (leaning spokes)
    window_fillet: float = 5.0
    edge_break: float = 1.5         # 45° chamfer on window rims (0 = sharp)
    facet_deg: float = 20.0         # 0 = flat spoke tops
    # Spoke grooves: lateral centre as a fraction of half width (0 = on the ridge).
    groove_offsets: tuple = (0.62,)
    groove_w: float = 4.0
    groove_depth: float = 3.0
    # Back weight pockets: U-channel under each spoke (0 skin = off).
    back_pocket_skin: float = 0.0   # material left under the machined top, mm
    back_pocket_wall: float = 4.5   # side walls left each side, mm
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
    """Footprint polygons and centre segments of spoke 0 along +X, swept.

    A segment is (path, width, knots): `path` is the spoke centreline (a polyline that follows
    arm bow and sweep, so facets, grooves and pockets stay centred); `width` is the nominal width
    used by facets and grooves; knots are (t, width) pairs along the path giving the real footprint
    width, for back pockets.
    """
    polys, segments = _straight_spoke(p)
    if not p.spoke_sweep_deg:
        return polys, segments

    def sweep(pt):
        r = math.hypot(*pt)
        t = min(max((r - p.window_r_in) / (p.window_r_out - p.window_r_in), 0), 1)
        a = math.radians(p.spoke_sweep_deg) * t ** 1.5
        return pt[0] * math.cos(a) - pt[1] * math.sin(a), pt[0] * math.sin(a) + pt[1] * math.cos(a)
    dense = [[sweep(q) for q in _densify(poly, 4.0)] for poly in polys]
    return dense, [([sweep(q) for q in path], w, knots) for path, w, knots in segments]


def _densify(poly, step):
    """Insert points so a curved sweep bends the edges instead of just moving vertices."""
    out = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        k = max(1, int(math.dist(a, b) / step))
        out += [(a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k) for i in range(k)]
    return out


def _skeleton_spoke(p):
    """Footprint = tapered quad per edge + round joint at every node with 2+ edges."""
    nodes = {k: polar(r, a) for k, (r, a) in p.skeleton['nodes'].items()}
    degree = {}
    polys, segments = [], []
    for a, b, wa, wb in p.skeleton['edges']:
        (ax, ay), (bx, by) = nodes[a], nodes[b]
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        nx, ny = -uy, ux
        ax, ay, bx, by = ax - ux, ay - uy, bx + ux, by + uy       # 1 mm overlap into the joints
        polys.append([(ax + nx * wa / 2, ay + ny * wa / 2), (bx + nx * wb / 2, by + ny * wb / 2),
                      (bx - nx * wb / 2, by - ny * wb / 2), (ax - nx * wa / 2, ay - ny * wa / 2)])
        clipped = _clip_to_band(p, nodes[a], nodes[b], wa, wb)
        if clipped:
            segments.append(clipped)
        for key, w in ((a, wa), (b, wb)):
            degree.setdefault(key, []).append(w)
    for key, widths in degree.items():
        if len(widths) > 1:
            x, y = nodes[key]
            r = max(widths) / 2
            polys.append([(x + r * math.cos(t), y + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 32, endpoint=False)])
    return polys, segments


def _clip_to_band(p, a, b, wa, wb):
    """Centre segment a->b restricted to the window band, so face/back cutters stay off hub and ring."""
    ts = np.linspace(0, 1, 401)
    inside = [t for t in ts if p.window_r_in <= math.hypot(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) <= p.window_r_out]
    if len(inside) < 2 or inside[-1] - inside[0] < .05:
        return None
    t0, t1 = inside[0], inside[-1]
    point = lambda t: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    width = lambda t: wa + (wb - wa) * t
    return [point(t0), point(t1)], max(width(t0), width(t1)), ((0.0, width(t0)), (1.0, width(t1)))


def _straight_spoke(p):
    if p.family == 'skeleton':
        return _skeleton_spoke(p)
    hub_w, hub_in = p.stem_w_hub / 2, p.window_r_in - 30
    if p.family == 'single':
        end_r = p.window_r_out + 10
        half = p.stem_w_split / 2
        side = [(hub_in, hub_w + 6), (p.window_r_in, hub_w),
                (p.window_r_in + 25, hub_w + (half - hub_w) * .25), (end_r, half)]
        span = p.window_r_out - p.window_r_in
        radii = [x for x, _ in side[1:]]
        knots = tuple(((r - p.window_r_in) / span, 2 * float(np.interp(r, radii, [y for _, y in side[1:]])))
                      for r in (p.window_r_in, p.window_r_in + 25, p.window_r_out))
        path = [(r, 0.0) for r in np.linspace(p.window_r_in, p.window_r_out, 9)]
        return [side + [(x, -y) for x, y in side[::-1]]], [(path, p.stem_w_split, knots)]
    split = (p.split_r, 0.0)
    stem_pts = [(hub_in, hub_w + 6), (p.window_r_in, hub_w),
                (p.window_r_in + 16, p.stem_w_split / 2 + 3), (p.split_r, p.stem_w_split / 2)]
    stem = stem_pts + [(x, -y) for x, y in stem_pts[::-1]]
    stem_knots = ((0.0, p.stem_w_hub), (16 / (p.split_r - p.window_r_in), p.stem_w_split + 6), (1.0, p.stem_w_split))
    stem_path = [(r, 0.0) for r in np.linspace(p.window_r_in, p.split_r, 5)]
    arms, segments = [], [(stem_path, p.stem_w_split, stem_knots)]
    for sign in (1, -1):
        end = polar(p.window_r_out + 10, sign * p.arm_angle_deg)
        dx, dy = end[0] - split[0], end[1] - split[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length

        def arm_centre(t, dx=dx, dy=dy, nx=nx, ny=ny, sign=sign):
            bow = p.arm_bow * math.sin(math.pi * t) * sign
            return split[0] - 12 * (1 - t) + dx * t + nx * bow, split[1] + dy * t + ny * bow
        centre = [arm_centre(t) for t in np.linspace(0, 1, 7)]
        half = p.arm_w / 2
        left = [(x + nx * half, y + ny * half) for x, y in centre]
        right = [(x - nx * half, y - ny * half) for x, y in centre]
        arms.append(left + right[::-1])
        # Features run on the same bowed centreline, from the split out to the window edge.
        us = [u for u in np.linspace(0, 1, 201) if p.split_r <= math.hypot(*arm_centre(u)) <= p.window_r_out]
        path = [arm_centre(u) for u in np.linspace(us[0], us[-1], 9)]
        segments.append((path, p.arm_w, ((0.0, p.arm_w), (1.0, p.arm_w))))
    return [stem] + arms, segments


def window_outlines(p, samples=160):
    """Closed window outlines (x, y) lists, corner-rounded, resampled evenly."""
    polys, _ = spoke_geometry(p)
    pitch = 360 / p.spokes
    sk = cq.Sketch().circle(p.window_r_out).circle(p.window_r_in, mode='s')
    for i in range(p.spokes):
        c, s = math.cos(math.radians(i * pitch)), math.sin(math.radians(i * pitch))
        for poly in polys:
            pts = [(x * c - y * s, x * s + y * c) for x, y in poly]
            sk = sk.polygon(pts + [pts[0]], mode='s')
    outlines = []
    for face in sk._faces.Faces():
        wire = round_corners(face, p.window_fillet).outerWire()
        outlines.append([wire.positionAt(i / samples).toTuple()[:2] for i in range(samples)])
    return outlines


def windows(p, outlines):
    """One periodic spline per window, so each window has a single smooth wall."""
    tools = []
    for pts in outlines:
        edge = cq.Edge.makeSpline([cq.Vector(x, y, -p.width) for x, y in pts], periodic=True)
        face = cq.Face.makeFromWires(cq.Wire.assembleEdges([edge]))
        tools.append(cq.Solid.extrudeLinear(face, cq.Vector(0, 0, p.width + 20)))
    return cq.Compound.makeCompound(tools)


def top_height(body):
    """Highest body surface z along a vertical line through (x, y)."""
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    inter = BRepIntCurveSurface_Inter()

    def height(x, y):
        inter.Init(body.wrapped, gp_Lin(gp_Pnt(x, y, 200), gp_Dir(0, 0, -1)), 1e-6)
        best = None
        while inter.More():
            best = inter.Pnt().Z() if best is None else max(best, inter.Pnt().Z())
            inter.Next()
        return best
    return height


def rim_break_cutters(p, body, outlines):
    """45° edge break along every window rim, as a chamfer mill would cut it.

    Each rim sample gets an upright triangle normal to the rim, from `edge_break` below the machined
    top (ray-cast on the actual body, so facets, hub and ring are followed) rising at 45° into the
    material; consecutive sections are lofted into short ruled wedges. This leaves small facets
    (teeth) along the chamfer. Smooth alternatives failed on 2026-09-23: B-Rep fillet/chamfer on the
    faceted rims, a pipe sweep (hung in OCC), and chunked smooth lofts (invalid Boolean/clean).
    """
    c, e = p.edge_break, 3.0
    height = top_height(body)
    wedges = []
    for pts in outlines:
        area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
        sign = 1 if area > 0 else -1          # CCW window: material is on the right
        n = len(pts)
        sections = []
        for i in range(n):
            (xa, ya), (xb, yb) = pts[i - 1], pts[(i + 1) % n]
            tx, ty = xb - xa, yb - ya
            length = math.hypot(tx, ty)
            nx, ny = sign * ty / length, -sign * tx / length
            x, y = pts[i]
            z = height(x + nx * .4, y + ny * .4)
            sections.append(None if z is None else cq.Wire.makePolygon(
                # The 45° face runs on 0.5 mm into the window: a corner exactly on the wall made
                # the combined Boolean silently remove nothing.
                [cq.Vector(x - nx * 2, y - ny * 2, z - c - .5), cq.Vector(x - nx * 2, y - ny * 2, z + e),
                 cq.Vector(x + nx * (c + e), y + ny * (c + e), z + e),
                 cq.Vector(x - nx * .5, y - ny * .5, z - c - .5)], close=True))
        for a, b in zip(sections, sections[1:] + sections[:1]):
            if a and b:                        # rim not found at a sample: that stretch stays sharp
                wedges.append(cq.Solid.makeLoft([a, b], ruled=True))
    return wedges


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


def segment_loft(p, path, section, n=13):
    """Ruled loft of quads section(fade, point_at, t) placed along the centreline polyline `path`.

    `point_at(s)` gives the point at lateral offset s, normal to the local path direction.
    """
    pts = np.array(path, dtype=float)
    cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    total = cum[-1]
    wires = []
    for t in np.linspace(0, 1, n):
        fade = min(1.0, t / .18, (1 - t) / .18)
        at_len = lambda u: np.array([np.interp(u * total, cum, pts[:, 0]), np.interp(u * total, cum, pts[:, 1])])
        cx, cy = at_len(t)
        tx, ty = at_len(min(t + .02, 1)) - at_len(max(t - .02, 0))
        length = math.hypot(tx, ty)
        nx, ny = -ty / length, tx / length
        quad = section(fade, lambda s: (cx + nx * s, cy + ny * s), t)
        wires.append(cq.Wire.makePolygon([cq.Vector(*q) for q in quad], close=True))
    return cq.Solid.makeLoft(wires, ruled=True)


def facet_cutters(p):
    """Two sloped cuts per segment leave a ridge; lift 0.5 above the face at the ends (no cut)."""
    if p.facet_deg <= 0:
        return []
    tan = math.tan(math.radians(p.facet_deg))
    cutters = []
    for path, width, knots in spoke_geometry(p)[1]:
        for side in (1, -1):
            def section(fade, at, t, side=side, reach=width / 2 + 5):
                lift = .5 - .8 * fade
                low = []
                for s in (-1.5 * side, reach * side):
                    x, y = at(s)
                    low.append((x, y, z_top(p, math.hypot(x, y)) + lift - side * s * tan * fade))
                return low + [(x, y, z_top(p, math.hypot(x, y)) + 25) for x, y, _ in low[::-1]]
            cutters.append(segment_loft(p, path, section))
    return cutters


def groove_cutters(p):
    """Channels milled into the finished spoke top, parallel to the facet slope."""
    cutters = []
    for path, width, knots in spoke_geometry(p)[1]:
        for offset in p.groove_offsets:
            for side in ((1, -1) if offset else (1,)):
                centre = side * offset * width / 2

                def section(fade, at, t, centre=centre):
                    low = []
                    for s in (centre - p.groove_w / 2, centre + p.groove_w / 2):
                        x, y = at(s)
                        low.append((x, y, face_height(p, x, y, s, fade) + .5 - (p.groove_depth + .5) * fade))
                    return low + [(x, y, z + 30) for x, y, z in low[::-1]]
                cutters.append(segment_loft(p, path, section))
    return cutters


def back_pocket_cutters(p):
    """Channels milled up from the back of each spoke segment, leaving walls and a top skin.

    The pocket roof follows the machined top (facets included) minus `back_pocket_skin`; at the
    segment ends it fades to 0.5 mm below the back face, so no cut reaches the hub or ring.
    """
    if p.back_pocket_skin <= 0:
        return []
    cutters = []
    for path, width, knots in spoke_geometry(p)[1]:
        ts, ws = zip(*knots)
        if min(ws) / 2 - p.back_pocket_wall < 2:
            continue

        def section(fade, at, t, ts=ts, ws=ws):
            half = float(np.interp(t, ts, ws)) / 2 - p.back_pocket_wall
            pts = []
            for s in (-half, half):
                x, y = at(s)
                r = math.hypot(x, y)
                floor = z_back(p, r) - .5
                roof = face_height(p, x, y, s, 1.0) - p.back_pocket_skin
                pts.append((x, y, floor + (roof - floor) * fade))
            return pts + [(x, y, z_back(p, math.hypot(x, y)) - 20) for x, y, _ in pts[::-1]]
        cutters.append(segment_loft(p, path, section))
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
        start, before = time.time(), volume(body)
        pitch = 360 / p.spokes
        for i in range(p.spokes if rotate else 1):
            for tool in tools:
                body = body.cut(tool.rotate((0, 0, 0), (0, 0, 1), i * pitch) if rotate else tool)
        body = body.clean()
        stages.append({'op': name, 'removed_mm3': round(before - volume(body), 1),
                       'valid': body.isValid(), 'solids': len(body.Solids()),
                       'seconds': round(time.time() - start, 1)})

    apply('face_facets', facet_cutters(p), rotate=True)
    outlines = window_outlines(p)
    apply('through_windows', [windows(p, outlines)])
    if p.edge_break > 0:
        apply('window_rim_edge_break', [cq.Compound.makeCompound(rim_break_cutters(p, body, outlines))])
        # Around tight window tips the wedge ends overlap and leave loose ~1 mm³ chips floating in
        # the window; no real tool leaves those, so drop them (and say so) if they are that small.
        solids = sorted(body.Solids(), key=volume, reverse=True)
        chips = sum(volume(x) for x in solids[1:])
        if len(solids) > 1 and chips < 50:
            body = solids[0]
            stages[-1].update(solids=1, dropped_chips=len(solids) - 1, dropped_chip_mm3=round(chips, 1))
        # This Boolean has silently cut nothing or part of the rim: compare with rim length x c²/2,
        # widened where facets slope the top up into the material.
        rim = sum(math.dist(a, b) for pts in outlines for a, b in zip(pts, pts[1:] + pts[:1]))
        expected = rim * p.edge_break ** 2 / 2
        widen = 1 / (1 - math.tan(math.radians(p.facet_deg))) ** 2
        ratio = stages[-1]['removed_mm3'] / expected
        stages[-1].update(expected_flat_mm3=round(expected, 1),
                          status='ok' if .9 < ratio < 1.2 * widen and len(body.Solids()) == 1 else 'suspect')
    apply('spoke_grooves', groove_cutters(p), rotate=True)
    apply('back_pockets', back_pocket_cutters(p), rotate=True)
    apply('lip_pockets', lip_pockets(p))
    apply('lug_holes_and_seats', lug_tools(p))
    return stock, body, stages


def recipe_from_dict(data: dict) -> ForgedWheel:
    """Validated ForgedWheel from a recipe dict; unknown keys and '_' render hints are rejected/dropped."""
    data = {k: v for k, v in (data or {}).items() if not k.startswith('_')}
    known = {f.name for f in fields(ForgedWheel)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"锻坯配方包含未知字段：{unknown}")
    for key in ('lip_pocket_r', 'groove_offsets'):
        if key in data:
            data[key] = tuple(data[key])
    p = replace(ForgedWheel(), **data)
    if p.family not in ('y_split', 'single', 'skeleton'):
        raise ValueError(f"未知轮辐结构：{p.family}")
    if p.family == 'skeleton' and not (p.skeleton and p.skeleton.get('nodes') and p.skeleton.get('edges')):
        raise ValueError("skeleton 结构需要 nodes 与 edges。")
    if not 3 <= p.spokes <= 12:
        raise ValueError("轮辐组数应在 3–12 之间。")
    return p


def export_model(recipe: dict, output, snapshot: dict | None = None) -> dict:
    """Build, check and write wheel.step / wheel.glb / stock.step / recipe.json / report.json.

    Coordinates follow the main template: Z is the axis, Z=0 the rim-width mid-plane, +Z the face.
    """
    import hashlib
    import json
    from pathlib import Path

    from .mass_properties import measure_volume

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    p = recipe_from_dict(recipe)
    stock, part, stages = build(p)
    shift = cq.Vector(0, 0, p.width / 2)
    stock, part = stock.translate(shift), part.translate(shift)
    measurement = measure_volume(part)
    bbox = part.BoundingBox()
    checks = {"valid_brep": bool(part.isValid()), "single_solid": len(part.Solids()) == 1,
              "positive_volume": measurement.volume_mm3 > 0}
    suspect = [s["op"] for s in stages if s.get("status") == "suspect" or s.get("solids", 1) != 1]
    if not all(checks.values()):
        raise ValueError(f"实体检查未通过：{checks}，可疑工序：{suspect}")
    step = output / "wheel.step"
    cq.exporters.export(part, str(step))
    reopened = cq.importers.importStep(str(step)).val()
    delta = abs(volume(reopened) - measurement.volume_mm3) / measurement.volume_mm3
    checks["step_roundtrip"] = reopened.isValid() and len(reopened.Solids()) == 1 and delta < 5e-5
    cq.exporters.export(stock, str(output / "stock.step"))
    cq.Assembly(part, color=cq.Color(.55, .53, .5)).export(str(output / "wheel.glb"))
    if not (output / "recipe.json").exists():
        (output / "recipe.json").write_text(json.dumps(snapshot or {"forged": asdict(p)}, ensure_ascii=False, indent=2))
    limitations = [
        "锻坯模板：所有尺寸为设计假设（按商品图目测），非实测；未做强度、疲劳或加工验证。",
        "轮辋为简化截面，未按 ETRTO / TRA 核对胎圈座与轮缘。",
        "窗口棱边为逐点楔形 45° 倒角，表面有细小台阶，未达 CAM 直接使用精度。",
    ]
    if suspect:
        limitations.insert(0, f"以下工序结果可疑，须复核：{', '.join(suspect)}")
    report = {
        "checks": checks, "solid_count": len(part.Solids()),
        "volume_mm3": round(measurement.volume_mm3, 3), "volume_measurement": measurement.to_dict(),
        "bbox_mm": [round(v, 4) for v in (bbox.xlen, bbox.ylen, bbox.zlen)],
        "face_count": len(part.Faces()), "step_volume_relative_delta": delta,
        "template_version": TEMPLATE_VERSION, "units": "mm",
        "coordinates": "右手系，轮毂轴线为 Z，轮辋宽度中面 Z=0，+Z 为外侧（装饰面）",
        "status": "geometry_checked", "engineering_approved": False, "manufacturing_status": "not_released",
        "forged": {"stages": stages, "removal_ratio": round(1 - measurement.volume_mm3 / volume(stock), 4),
                   "part_mass_kg_6061": round(measurement.volume_mm3 * 2700 / 1e9, 2),
                   "stock_volume_mm3": round(volume(stock), 1), "suspect_operations": suspect},
        "model_id": (snapshot or {}).get("model_id"), "draft_revision": (snapshot or {}).get("draft_revision"),
        "limitations": limitations,
    }
    report["artifacts"] = {name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                                  "bytes": (output / name).stat().st_size}
                           for name in ("wheel.step", "wheel.glb", "stock.step", "recipe.json") if (output / name).exists()}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
