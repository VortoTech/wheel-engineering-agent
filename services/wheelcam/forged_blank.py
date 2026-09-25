"""Forged-blank wheel template: build a wheel in machining order from one parameter set.

revolved forging blank -> face facets -> through windows (2D sketch) -> stem slots -> window pockets under the lip
-> spoke grooves -> back weight pockets -> lip-face pockets -> lug holes and seats.

Prototype origin and design notes: experiments/forged-blank/README.md. All default dimensions are
design assumptions, not measurements; manufacturing status is always not_released.

Window-rim edge breaks are left to CAM (report `cam_operations`): every attempt to model them in the
B-Rep either multiplied STEP size 3-11x (per-sample wedges) or failed in OCC Booleans on faceted and
grooved spokes (smooth ring tools, 2026-09-23/24). CAM chamfers sharp edges directly.
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
    family: str = 'y_split'         # also 'single', 'skeleton' (graph from `skeleton`) and 'outline'
    # outline family: one group's window outlines, each a closed list of [r_mm, angle_deg] relative to
    # spoke 0's axis (traced from a photo). Parts beyond ring_r - 2 become blind window pockets.
    outlines: tuple = ()
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
    # Spoke side flanks: a slope cut along every through-window edge, flank_w wide at the face and
    # flank_depth deep, leaving a narrower spoke top (0 = vertical window walls).
    flank_w: float = 0.0
    flank_depth: float = 0.0
    # Face machining surface (0 width = off): the whole face is cut last by one smooth B-spline surface,
    # dish profile minus a rounded shoulder that drops face_crown_depth at every window edge over
    # face_crown_w (superellipse exponent face_crown_q: 1 = straight chamfer, 2 = quarter round, >2 = flatter top).
    # Replaces facets / grooves / flanks, gives rounded spoke crowns and smooth fork, hub and lip blends.
    face_crown_w: float = 0.0
    face_crown_depth: float = 12.0
    face_crown_q: float = 2.5
    face_grid_mm: float = 2.0
    # Window pockets: each window continues outward as a blind pocket over the lip-to-barrel slope,
    # so the spokes run out to the lip (deep-concave style). 0 = off; the radius is the pocket's outer edge.
    window_pocket_r: float = 0.0
    window_pocket_depth: float = 24.0
    # Through slots in spoke 0's frame, [r_from, r_to, lateral_offset, width] each; offset > 0 = a
    # mirrored pair (stem slots beside the lugs), 0 = one slot on the spoke axis (hole ahead of a fork).
    stem_slots: tuple = ()
    edge_break: float = 1.5         # 45° chamfer on window rims, machined in CAM (0 = none); CAD keeps sharp edges
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
    # Styling pocket around each lug on the hub face (0 = off): a shallow counterbore wider than the seat.
    lug_pocket_d: float = 0.0
    lug_pocket_depth: float = 8.0
    lug_pocket_sides: int = 0       # 0 = round; 6 = hexagon (across corners = lug_pocket_d), corner toward the hub centre


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


FACE_LIFT = 12.0     # blank front stock above the face surface, so the surface cut never grazes the blank
FLANK_SHARE = .45    # widest flank as a share of the local spoke width (two flanks leave a 10 % land)


def blank(p):
    """Revolved forging blank: hub, concave face web, lip face ring and barrel.

    With a face machining surface the whole front is raised by FACE_LIFT (forging stock that the
    surfacing pass removes); otherwise the blank front is the finished dish.
    """
    lift = FACE_LIFT if p.face_crown_w > 0 else 0.0
    rs = np.linspace(p.hub_r, p.ring_r, 9)
    front = [(r, z_top(p, r) + lift) for r in rs]
    back = [(r, z_back(p, r)) for r in rs[::-1]]
    lip_back = -min(14.0, p.lip_r - p.barrel_outer_r)
    wp = (cq.Workplane('XZ').moveTo(p.center_bore_r, p.hub_z + lift).lineTo(p.hub_r, p.hub_z + lift)
          .spline(front[1:], includeCurrent=True)
          .lineTo(p.lip_face_r_in, lift).lineTo(p.lip_r, lift).lineTo(p.lip_r, lip_back)
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
    if p.family == 'outline':
        return [], []                              # windows are given directly; no centre segments
    if p.family == 'skeleton':
        return _skeleton_spoke(p)
    hub_w, hub_in = p.stem_w_hub / 2, p.window_r_in - 30
    reach = max(p.window_r_out, p.window_pocket_r) + 10
    if p.family == 'single':
        end_r = p.window_r_out + 10
        half = p.stem_w_split / 2
        side = [(hub_in, hub_w + 6), (p.window_r_in, hub_w),
                (p.window_r_in + 25, hub_w + (half - hub_w) * .25), (end_r, half)]
        if reach > end_r:
            side.append((reach, half))
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
            bow = p.arm_bow * math.sin(math.pi * min(t, 1)) * sign     # straight past the window edge
            return split[0] - 12 * (1 - t) + dx * t + nx * bow, split[1] + dy * t + ny * bow
        # Extend past the defining end point (window_r_out + 10) to reach the pocket edge.
        t_end = 1.0
        while math.hypot(*arm_centre(t_end)) < reach:
            t_end += .02
        centre = [arm_centre(t) for t in np.linspace(0, 1, 7)] + ([arm_centre(t_end)] if t_end > 1 else [])
        half = p.arm_w / 2
        left = [(x + nx * half, y + ny * half) for x, y in centre]
        right = [(x - nx * half, y - ny * half) for x, y in centre]
        arms.append(left + right[::-1])
        # Features run on the same bowed centreline, from the split out to the window edge.
        us = [u for u in np.linspace(0, 1, 201) if p.split_r <= math.hypot(*arm_centre(u)) <= p.window_r_out]
        path = [arm_centre(u) for u in np.linspace(us[0], us[-1], 9)]
        segments.append((path, p.arm_w, ((0.0, p.arm_w), (1.0, p.arm_w))))
    return [stem] + arms, segments


def _outline_faces(p):
    """Every traced window of the outline family, all groups, as planar faces at z = 0."""
    faces = []
    for i in range(p.spokes):
        for outline in p.outlines:
            pts = [cq.Vector(*polar(r, a + i * 360 / p.spokes), 0) for r, a in outline]
            edge = cq.Edge.makeSpline(pts, periodic=True)
            faces.append(cq.Face.makeFromWires(cq.Wire.assembleEdges([edge])))
    return faces


def _disc(r):
    return cq.Face.makeFromWires(cq.Wire.makeCircle(r, cq.Vector(), cq.Vector(0, 0, 1)))


def window_outlines(p, samples=160):
    """Closed window outlines (x, y) lists, corner-rounded, resampled evenly."""
    if p.family == 'outline':
        disc = _disc(p.ring_r - 2)
        outlines = []
        for face in _outline_faces(p):
            for part in face.intersect(disc).Faces():
                wire = part.outerWire()
                outlines.append([wire.positionAt(i / samples).toTuple()[:2] for i in range(samples)])
        return outlines
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
    """One periodic spline per window, so each window has a single smooth wall.

    Flanked windows are returned as a list and cut one at a time: their widened tops overlap
    where a spoke is narrower than two flanks, and one cut with an overlapping compound made OCC
    grow past 90 GB (2026-09-24). Sequential cuts stay under 1 GB.
    """
    if p.flank_w > 0 and p.flank_depth > 0:
        from scipy.spatial import cKDTree
        loops = [np.asarray(pts, float) for pts in outlines]
        tools = []
        for i, xy in enumerate(loops):
            others = [o for j, o in enumerate(loops) if j != i]
            width = cKDTree(np.concatenate(others)).query(xy)[0] if others else np.full(len(xy), np.inf)
            tools.append(_flanked_window(p, pts=outlines[i], spoke_w=width))
        return tools
    tools = []
    for pts in outlines:
        edge = cq.Edge.makeSpline([cq.Vector(x, y, -p.width) for x, y in pts], periodic=True)
        face = cq.Face.makeFromWires(cq.Wire.assembleEdges([edge]))
        tools.append(cq.Solid.extrudeLinear(face, cq.Vector(0, 0, p.width + 20)))
    return cq.Compound.makeCompound(tools)


def _flanked_window(p, pts, spoke_w=None):
    """Window prism whose top widens by p.flank_w over p.flank_depth: ruled loft through four rings.

    Each outline point moves along its outward normal (into the material), so the rings keep point
    correspondence and the flank rules straight across. Outlines are smooth (fillets / traced and
    smoothed), so the offset does not fold except at concave bends tighter than p.flank_w, where the
    offset is limited by the local bend radius.

    `spoke_w` is the material width to the nearest other window at each point. The offset is held
    to FLANK_SHARE of it, so on a spoke narrower than two flanks the flanks from both sides meet in
    a ridge with a narrow land instead of overlapping (overlapping flanks made an invalid B-Rep).
    """
    xy = np.asarray(pts, float)
    ring = lambda k: np.roll(xy, k, axis=0)
    tangent = ring(-1) - ring(1)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    normal = np.column_stack([tangent[:, 1], -tangent[:, 0]])
    area = .5 * np.sum(xy[:, 0] * ring(-1)[:, 1] - ring(-1)[:, 0] * xy[:, 1])
    if area < 0:                                   # clockwise loop: flip to point out of the window
        normal = -normal
    # Signed bend: a concave bend (material bulging into the window) folds an offset wider than its radius.
    d1, d2 = ring(-1) - xy, xy - ring(1)
    cross = d2[:, 0] * d1[:, 1] - d2[:, 1] * d1[:, 0]
    seg = np.linalg.norm(d1, axis=1)
    angle = np.arcsin(np.clip(cross / np.maximum(seg * np.linalg.norm(d2, axis=1), 1e-9), -1, 1)) * np.sign(area)
    radius = np.where(angle < -1e-6, seg / np.maximum(-angle, 1e-9), np.inf)
    reach = np.minimum(p.flank_w, .8 * radius)
    if spoke_w is not None:
        reach = np.minimum(reach, FLANK_SHARE * np.asarray(spoke_w))
    from scipy.ndimage import gaussian_filter1d
    reach = gaussian_filter1d(reach, 2, mode='wrap')
    wide = xy + normal * reach[:, None]
    z = lambda q: np.array([z_top(p, math.hypot(*v)) for v in q])
    rings = [np.column_stack([xy, np.full(len(xy), -p.width - 10.0)]),
             np.column_stack([xy, z(xy) - p.flank_depth]),
             np.column_stack([wide, z(wide) + 1.0]),
             np.column_stack([wide, np.full(len(xy), 40.0)])]
    wires = [cq.Wire.assembleEdges([cq.Edge.makeSpline([cq.Vector(*v) for v in r], periodic=True)]) for r in rings]
    return cq.Solid.makeLoft(wires, ruled=True)


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


def _window_envelope(p):
    """Where an outline-family window may cut: through inside the ring, only down to the pocket floor beyond."""
    floor = -p.window_pocket_depth
    ring = p.ring_r - 2
    profile = (cq.Workplane('XZ').moveTo(0, -p.width - 30).lineTo(ring, -p.width - 30).lineTo(ring, floor)
               .lineTo(p.lip_r + 10, floor).lineTo(p.lip_r + 10, 80).lineTo(0, 80).close())
    return profile.revolve(360, (0, 0, 0), (0, 1, 0)).val()


def outline_window_tools(p, samples=200):
    """One tool per traced window: the whole outline (through part and pocket part) cut by a single
    prism or flanked loft, trimmed to the window envelope. Separate through and pocket tools had
    nearly coincident side walls (two resampled splines) and left sliver faces where they met.
    """
    outlines = []
    for face in _outline_faces(p):
        wire = face.outerWire()
        outlines.append([wire.positionAt(i / samples).toTuple()[:2] for i in range(samples)])
    envelope = _window_envelope(p)
    tools = windows(p, outlines)
    return [t.intersect(envelope) for t in (tools if isinstance(tools, list) else [tools])]


def window_pocket_tools(p):
    """Blind pockets continuing every window out to `window_pocket_r`, floor at -window_pocket_depth."""
    if p.family == 'outline':
        return []                                  # part of outline_window_tools
    if p.window_pocket_r <= p.window_r_out:
        return []
    polys, _ = spoke_geometry(p)
    pitch = 360 / p.spokes
    sk = cq.Sketch().circle(p.window_pocket_r).circle(p.window_r_out - 4, mode='s')
    for i in range(p.spokes):
        c, s = math.cos(math.radians(i * pitch)), math.sin(math.radians(i * pitch))
        for poly in polys:
            pts = [(x * c - y * s, x * s + y * c) for x, y in poly]
            sk = sk.polygon(pts + [pts[0]], mode='s')
    tools = []
    for face in sk._faces.Faces():
        face = round_corners(face, p.window_fillet).translate(cq.Vector(0, 0, -p.window_pocket_depth))
        tools.append(cq.Solid.extrudeLinear(face, cq.Vector(0, 0, p.window_pocket_depth + 10)))
    return tools


def slot_tools(p):
    """Through slots of spoke 0 (rotated per spoke by the caller)."""
    tools = []
    for r0, r1, offset, width in p.stem_slots:
        for y in ((offset, -offset) if offset else (0.0,)):
            tools.append(cq.Workplane('XY', origin=(0, 0, -p.width)).center((r0 + r1) / 2, y)
                         .slot2D(r1 - r0, width).extrude(p.width + 20).val())
    return tools


def face_profile(p, r):
    """Finished dish height at radius r: hub plateau, concave web, slope up to the lip face, lip face."""
    r = np.asarray(r, float)
    web = np.vectorize(lambda v: z_top(p, v))(np.minimum(r, p.ring_r))
    slope = np.interp(r, [p.ring_r, p.lip_face_r_in], [p.ring_z, 0.0])
    return np.where(r <= p.ring_r, web, np.where(r <= p.lip_face_r_in, slope, 0.0))


def window_mask(p, half, res):
    """Raster (res mm) of every window seen from the front: through part, pockets and stem slots."""
    from PIL import Image, ImageDraw
    n = int(round(2 * half / res))
    img = Image.new('L', (n, n), 0)
    draw = ImageDraw.Draw(img)
    to_px = lambda pts: [((x + half) / res, (y + half) / res) for x, y in pts]
    if p.family == 'outline':
        polys = [[polar(r, a + i * 360 / p.spokes) for r, a in o] for i in range(p.spokes) for o in p.outlines]
    else:
        polys = [list(o) for o in window_outlines(p, samples=240)]
    for r0, r1, offset, width in p.stem_slots:
        t = np.linspace(-math.pi / 2, math.pi / 2, 16)
        for y in ((offset, -offset) if offset else (0.0,)):
            ends = [(r1 - width / 2 + width / 2 * math.cos(a), y + width / 2 * math.sin(a)) for a in t]
            ends += [(r0 + width / 2 - width / 2 * math.cos(a), y - width / 2 * math.sin(a)) for a in t]
            for i in range(p.spokes):
                c, s_ = math.cos(math.radians(i * 360 / p.spokes)), math.sin(math.radians(i * 360 / p.spokes))
                polys.append([(x * c - v * s_, x * s_ + v * c) for x, v in ends])
    for poly in polys:
        draw.polygon(to_px(poly), fill=255)
    return np.asarray(img) > 0


def face_surface_tool(p):
    """Solid above the face machining surface (one seamless B-spline over the whole face).

    Height = face_profile(r) - shoulder(d), d = distance to the nearest window edge. The drop field
    is smoothed by one grid step before sampling (a steep edge sampled at the grid spacing showed as
    ripples along the spokes), and the grid points are used directly as the poles of a cubic
    B-spline: no fitting (least-squares fitting of the 80 k points took > 10 minutes), smoothing ~ 1 step.
    One patch, not per-sector patches: near-coincident overlapping patches split the part in OCC.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.Geom import Geom_BSplineSurface
    from OCP.TColgp import TColgp_Array2OfPnt
    from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
    from OCP.gp import gp_Pnt
    from scipy.ndimage import distance_transform_edt, gaussian_filter, map_coordinates

    res, step = .5, p.face_grid_mm
    half = p.lip_r + 8
    dist = distance_transform_edt(~window_mask(p, half, res)) * res
    s = np.clip(1 - dist / p.face_crown_w, 0, 1)
    drop = gaussian_filter(p.face_crown_depth * (1 - (1 - s ** p.face_crown_q) ** (1 / p.face_crown_q)), step / res)
    xs = np.arange(-half + 2, half - 2 + step / 2, step)
    gx, gy = np.meshgrid(xs, xs, indexing='ij')
    z = face_profile(p, np.hypot(gx, gy)) - map_coordinates(drop, [(gy + half) / res, (gx + half) / res], order=1, mode='nearest')
    n, deg = len(xs), 3
    poles = TColgp_Array2OfPnt(1, n, 1, n)
    for i in range(n):
        for j in range(n):
            poles.SetValue(i + 1, j + 1, gp_Pnt(float(gx[i, j]), float(gy[i, j]), float(z[i, j])))
    k = n - deg + 1
    knots, mults = TColStd_Array1OfReal(1, k), TColStd_Array1OfInteger(1, k)
    for i in range(k):
        knots.SetValue(i + 1, i / (k - 1))
        mults.SetValue(i + 1, deg + 1 if i in (0, k - 1) else 1)
    surface = Geom_BSplineSurface(poles, knots, knots, mults, mults, deg, deg)
    face = cq.Face(BRepBuilderAPI_MakeFace(surface, 1e-6).Face())
    return cq.Solid.extrudeLinear(face, cq.Vector(0, 0, FACE_LIFT + 60))


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
        if p.lug_pocket_d > p.seat_d:
            wp = cq.Workplane('XY', origin=(x, y, p.hub_z - p.lug_pocket_depth))
            if p.lug_pocket_sides >= 3:
                angle = math.degrees(math.atan2(y, x))
                corners = [polar(p.lug_pocket_d / 2, angle + 180 + k * 360 / p.lug_pocket_sides) for k in range(p.lug_pocket_sides)]
                sketch = cq.Sketch().polygon(corners + [corners[0]]).vertices().fillet(3)
                tools.append(wp.placeSketch(sketch).extrude(40).val())
            else:
                tools.append(wp.circle(p.lug_pocket_d / 2).extrude(40).val())
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

    surfaced = p.face_crown_w > 0
    if surfaced:
        # The face surface replaces facets / grooves / flanks; back pockets assume the unsurfaced top
        # and would break through the lowered spoke edges, so they wait for a surface-aware version.
        p = replace(p, facet_deg=0.0, groove_offsets=(), flank_w=0.0, back_pocket_skin=0.0)
    apply('face_facets', facet_cutters(p), rotate=True)
    if p.family == 'outline':
        apply('through_windows', outline_window_tools(p))
    else:
        cutters = windows(p, window_outlines(p))
        apply('through_windows', cutters if isinstance(cutters, list) else [cutters])
    apply('stem_slots', slot_tools(p), rotate=True)
    apply('window_pockets', window_pocket_tools(p))
    apply('spoke_grooves', groove_cutters(p), rotate=True)
    apply('back_pockets', back_pocket_cutters(p), rotate=True)
    apply('lip_pockets', lip_pockets(p))
    apply('lug_holes_and_seats', lug_tools(p))
    if surfaced:
        # Last: every other tool meets the flat or revolved blank. Cylinders (lug seats) and pocket
        # walls cut after the B-spline face returned null shapes in OCC (2026-09-24).
        apply('face_surface', [face_surface_tool(p)])
    return stock, body, stages


PRESET_NAMES = {
    "hf6-y-split": "6 组 Y 形分叉 · 深凹（20″，HF-6 风格）",
    "v12-hub-fork": "12 辐 · 中心分叉偏转（22″）",
    "tree6-branching": "网状分叉 · 24 窗（22″）",
    "wide6-centre-groove": "6 根宽直辐 · 中心槽（22″）",
    "work6-tapered": "6 根外宽辐 · 平面（16″，WORK 风格）",
}


def presets(directory=None) -> list[dict]:
    """Checked-in example recipes as full, validated recipe dicts (defaults if none are found)."""
    import json
    from pathlib import Path

    directory = Path(directory) if directory else Path(__file__).resolve().parents[2] / "experiments" / "forged-blank" / "recipes"
    items = []
    for path in sorted(directory.glob("*.json"), key=lambda p: list(PRESET_NAMES).index(p.stem) if p.stem in PRESET_NAMES else 99) if directory.is_dir() else []:
        try:
            items.append({"id": path.stem, "name": PRESET_NAMES.get(path.stem, path.stem),
                          "recipe": asdict(recipe_from_dict(json.loads(path.read_text())))})
        except (ValueError, TypeError):
            continue                               # a broken example must not break the panel
    return items or [{"id": "default", "name": "默认（6 组 Y 形分叉）", "recipe": asdict(ForgedWheel())}]


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
    if 'stem_slots' in data:
        data['stem_slots'] = tuple(tuple(float(v) for v in slot) for slot in data['stem_slots'])
        if any(len(slot) != 4 or slot[1] <= slot[0] or slot[3] <= 0 for slot in data['stem_slots']):
            raise ValueError("stem_slots 每项应为 [r_from, r_to, lateral_offset, width]，且 r_to > r_from、width > 0。")
    if 'outlines' in data:
        data['outlines'] = tuple(tuple((float(r), float(a)) for r, a in outline) for outline in data['outlines'])
    p = replace(ForgedWheel(), **data)
    if p.family not in ('y_split', 'single', 'skeleton', 'outline'):
        raise ValueError(f"未知轮辐结构：{p.family}")
    if p.family == 'outline' and not (p.outlines and all(len(o) >= 8 for o in p.outlines)):
        raise ValueError("outline 结构需要 outlines（每个窗口至少 8 个 [r, 角度] 点）。")
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

    from .mass_properties import measure_volume, volume_method

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
    # Mounting face (hub back) in main-template coordinates; relative to the rim mid-plane this is ET.
    mounting_face_z = z_back(p, p.hub_r) + p.width / 2
    from .models import Preparation
    from .preparation import check_preparation
    preparation = check_preparation(part, None, Preparation.model_validate((snapshot or {}).get("preparation") or {}),
                                    output, mounting_face_z_mm=mounting_face_z, export_stock=False)
    if not (output / "recipe.json").exists():
        (output / "recipe.json").write_text(json.dumps(snapshot or {"forged": asdict(p)}, ensure_ascii=False, indent=2))
    limitations = [
        "锻坯模板：所有尺寸为设计假设（按商品图目测），非实测；未做强度、疲劳或加工验证。",
        "轮辋为简化截面，未按 ETRTO / TRA 核对胎圈座与轮缘。",
        "窗口棱边在 CAD 中为锐边；棱边倒角作为加工工序交给 CAM（见 cam_operations）。",
    ]
    if suspect:
        limitations.insert(0, f"以下工序结果可疑，须复核：{', '.join(suspect)}")
    report = {
        "checks": checks, "solid_count": len(part.Solids()),
        "volume_mm3": round(measurement.volume_mm3, 3), "volume_measurement": measurement.to_dict(),
        "bbox_mm": [round(v, 4) for v in (bbox.xlen, bbox.ylen, bbox.zlen)],
        "face_count": len(part.Faces()),
        "derived": {"outer_diameter_mm": round(2 * p.lip_r, 1), "overall_width_mm": round(p.width, 1),
                    "offset_et_mm": round(mounting_face_z, 1), "concavity_mm": round(-p.hub_z, 1)},
        "preparation": preparation, "step_volume_relative_delta": delta, "step_volume_method": volume_method(),
        "template_version": TEMPLATE_VERSION, "units": "mm",
        "coordinates": "右手系，轮毂轴线为 Z，轮辋宽度中面 Z=0，+Z 为外侧（装饰面）",
        "status": "geometry_checked", "engineering_approved": False, "manufacturing_status": "not_released",
        "forged": {"stages": stages, "removal_ratio": round(1 - measurement.volume_mm3 / volume(stock), 4),
                   "part_mass_kg_6061": round(measurement.volume_mm3 * 2700 / 1e9, 2),
                   "stock_volume_mm3": round(volume(stock), 1), "suspect_operations": suspect},
        "cam_operations": [{"op": "window_rim_edge_break", "size_mm": p.edge_break, "angle_deg": 45,
                            "edges": "all through-window rims, front (face) side",
                            "note": "Not modelled in CAD; chamfer the sharp rim edges in CAM."}] if p.edge_break > 0 else [],
        "model_id": (snapshot or {}).get("model_id"), "draft_revision": (snapshot or {}).get("draft_revision"),
        "limitations": limitations,
    }
    report["artifacts"] = {name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                                  "bytes": (output / name).stat().st_size}
                           for name in ("wheel.step", "wheel.glb", "stock.step", "caliper-envelope.step", "recipe.json")
                           if (output / name).exists()}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
