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
import os
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
    # Hub crease (0 = off): the face runs straight from (hub_r, hub_z) to (hub_crease_r, hub_crease_z),
    # then dishes to the ring from there. The sharp crease across every spoke root is the edge of a
    # spoke-top platform (HF6-4: a steep chamfer down to the cap ring, a flat stem beyond).
    hub_crease_r: float = 0.0
    hub_crease_z: float = 0.0
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
    flank_share: float = .45        # widest flank as a share of the local spoke width (.45 x 2 leaves a 10 % land)
    # Face chamfer (0 = off): every window, flank and pad-pocket top edge gets a chamfer about this
    # size, built into the cutting tool (a transverse facet), so edges catch a highlight as a machined
    # edge break does. Fillets on the finished solid are too fragile in OCC for this body.
    face_chamfer: float = 0.0
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
    # outline family: one groove along the centreline of every spoke section between two traced
    # windows, inside the radial band outline_groove_r = (from, to) (0, 0 = off), where the spoke is
    # at least groove_w + 2 x GROOVE_LAND wide. Width and depth are groove_w / groove_depth.
    outline_groove_r: tuple = (0.0, 0.0)
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
    # Hub valleys (0 depth = off): the hub face between neighbouring arms is dropped, so the arms run
    # in as ridges to a ring round the bore and each lug sits in a valley (HF6-4 style). The valley's
    # top edge (what a front photo shows) follows the arm edges (arms hub_arm_w wide, straight) and
    # hub_valley_r = (inner, outer) radius (0 = auto: the first radius from bore + 6 whose floor fits, the window tips); walls drafted
    # hub_valley_draft_deg down to a flat floor.
    hub_valley_depth: float = 0.0
    hub_arm_w: float = 30.0
    hub_valley_r: tuple = (0.0, 0.0)
    hub_valley_draft_deg: float = 30.0
    # Spoke pads (0 width = off): the centre of every spoke stands proud as a flat pad spoke_pad_w wide
    # from spoke_pad_r[0] to spoke_pad_r[1]; the face either side of it (slots, side ribs) is lowered
    # spoke_pad_depth below the dish, with walls drafted spoke_pad_draft_deg, easing out toward the
    # fork so the arms keep full height (HF6-4 stem).
    spoke_pad_w: float = 0.0
    spoke_pad_depth: float = 6.0
    spoke_pad_r: tuple = (0.0, 0.0)
    spoke_pad_draft_deg: float = 35.0
    spoke_pad_share: float = .6      # a pad covers at most this share of the local spoke half width


def z_top(p, r):
    r = min(max(r, p.hub_r), p.ring_r)
    r0, z0 = p.hub_r, p.hub_z
    if p.hub_crease_r > p.hub_r:
        if r <= p.hub_crease_r:
            return p.hub_z + (p.hub_crease_z - p.hub_z) * (r - p.hub_r) / (p.hub_crease_r - p.hub_r)
        r0, z0 = p.hub_crease_r, p.hub_crease_z
    t = (r - r0) / (p.ring_r - r0)
    return z0 + (p.ring_z - z0) * t ** p.concavity_exp


def z_back(p, r):
    t = (min(max(r, p.hub_r), p.ring_r) - p.hub_r) / (p.ring_r - p.hub_r)
    return z_top(p, r) - (p.web_thick_hub + (p.web_thick_ring - p.web_thick_hub) * t)


def polar(r, deg):
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


FACE_LIFT = 12.0     # blank front stock above the face surface, so the surface cut never grazes the blank
POCKET_SKIN = 6.0    # least material under a window pocket floor, mm
# Widest flank as a share of the window's own size: a 16 mm flank round a 15 mm fork triangle
# offset it into a round blob with a thick dark ring (HF6-4 v6, 2026-09-25); small windows get a
# chamfer in proportion, as the photo shows.
WINDOW_SHARE = .5
# Below this a flank is left to the CAM edge break and the window cut straight: a 2-4 mm flank
# loft on the dish face made OCC cuts fail silently (fork triangles came out as posts, 2026-09-25).
MIN_FLANK = 4.0
SMALL_BEVEL_MAX = 4.0   # widest chamfer on a straight-walled small window, mm
GROOVE_LAND = 2.0    # least spoke top left each side of a centreline groove, mm


def blank(p):
    """Revolved forging blank: hub, concave face web, lip face ring and barrel.

    With a face machining surface the whole front is raised by FACE_LIFT (forging stock that the
    surfacing pass removes); otherwise the blank front is the finished dish.
    """
    lift = FACE_LIFT if p.face_crown_w > 0 else 0.0
    creased = p.hub_crease_r > p.hub_r
    start = p.hub_crease_r if creased else p.hub_r
    rs = np.linspace(start, p.ring_r, 9)
    front = [(r, z_top(p, r) + lift) for r in rs]
    back = [(r, z_back(p, r)) for r in np.linspace(p.hub_r, p.ring_r, 9)[::-1]]
    lip_back = -min(14.0, p.lip_r - p.barrel_outer_r)
    wp = cq.Workplane('XZ').moveTo(p.center_bore_r, p.hub_z + lift).lineTo(p.hub_r, p.hub_z + lift)
    if creased:                                  # a straight run to the crease keeps it sharp (a spline would round it)
        wp = wp.lineTo(p.hub_crease_r, p.hub_crease_z + lift)
    wp = (wp.spline(front[1:], includeCurrent=True)
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
        return [_loft_rings(rings) for rings in window_rings(p, outlines)]
    return cq.Compound.makeCompound([_prism_window(p, pts) for pts in outlines])


def window_rings(p, outlines):
    """Point rings of every window tool (bottom to top), shared by the B-Rep and the mesh build."""
    loops = [np.asarray(pts, float) for pts in outlines]
    if not (p.flank_w > 0 and p.flank_depth > 0):
        return [_prism_rings(p, xy, p.face_chamfer) for xy in loops]
    from scipy.spatial import cKDTree
    out = []
    for i, xy in enumerate(loops):
        others = [o for j, o in enumerate(loops) if j != i]
        width = cKDTree(np.concatenate(others)).query(xy)[0] if others else np.full(len(xy), np.inf)
        reach = WINDOW_SHARE * _window_size(xy)
        # Small windows: straight walls with a chamfer in proportion (the official fork triangle and
        # slots have a 3-4 mm dark bevel), instead of a thin flank loft that OCC cut badly.
        bevel = min(SMALL_BEVEL_MAX, max(p.face_chamfer, reach)) if p.face_chamfer > 0 else 0.0
        rings = None
        if reach >= MIN_FLANK:
            try:
                rings = _flank_rings(p, xy, spoke_w=width, max_reach=reach)
            except FoldError:
                bevel = min(SMALL_BEVEL_MAX, max(p.face_chamfer, 2.0))   # HF-3's slim windows (2026-09-26)
        out.append(rings if rings is not None else _prism_rings(p, xy, bevel))
    return out


def _outward_normal(xy):
    """Unit normals of a closed loop, pointing out of the region it bounds."""
    tangent = np.roll(xy, -1, axis=0) - np.roll(xy, 1, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    normal = np.column_stack([tangent[:, 1], -tangent[:, 0]])
    area = .5 * np.sum(xy[:, 0] * np.roll(xy, -1, axis=0)[:, 1] - np.roll(xy, -1, axis=0)[:, 0] * xy[:, 1])
    return -normal if area < 0 else normal


def _prism_window(p, pts, chamfer=None):
    """Straight-walled window: a periodic spline through the outline, extruded through the face
    (with a chamfer, default face_chamfer: a ring at the top, c deep and crossing the face c + 1 out)."""
    c = p.face_chamfer if chamfer is None else chamfer
    if c > 0:
        return _loft_rings(_prism_rings(p, pts, c))
    edge = cq.Edge.makeSpline([cq.Vector(x, y, -p.width) for x, y in pts], periodic=True)
    face = cq.Face.makeFromWires(cq.Wire.assembleEdges([edge]))
    return cq.Solid.extrudeLinear(face, cq.Vector(0, 0, p.width + 20))


def _prism_rings(p, pts, c):
    """Point rings (bottom to top) of a straight window, with a chamfer ring c deep when c > 0."""
    xy = np.asarray(pts, float)
    if c <= 0:
        return [np.column_stack([xy, np.full(len(xy), -p.width - 10.0)]), np.column_stack([xy, np.full(len(xy), 40.0)])]
    z = lambda q: np.array([z_top(p, math.hypot(*v)) for v in q])
    crest = _unfold(xy, _outward_normal(xy), np.full(len(xy), c + 1.0))
    return [np.column_stack([xy, np.full(len(xy), -p.width - 10.0)]), np.column_stack([xy, z(xy) - c]),
            np.column_stack([crest, z(crest) + 1.0]), np.column_stack([crest, np.full(len(xy), 40.0)])]


def _loft_rings(rings):
    """B-Rep tool through point rings: a periodic spline per ring, ruled between them."""
    wires = [cq.Wire.assembleEdges([cq.Edge.makeSpline([cq.Vector(*v) for v in r], periodic=True)]) for r in rings]
    return cq.Solid.makeLoft(wires, ruled=True)


def _window_size(xy):
    """Characteristic width of a window loop: 2 x area / perimeter (~0.75 x the width of a slot)."""
    area = abs(.5 * np.sum(xy[:, 0] * np.roll(xy, -1, axis=0)[:, 1] - np.roll(xy, -1, axis=0)[:, 0] * xy[:, 1]))
    return 2 * area / np.sum(np.linalg.norm(np.roll(xy, -1, axis=0) - xy, axis=1))


def _flanked_window(p, pts, spoke_w=None, max_reach=np.inf):
    """Window prism whose top widens by p.flank_w over p.flank_depth (see _flank_rings)."""
    return _loft_rings(_flank_rings(p, pts, spoke_w, max_reach))


def _flank_rings(p, pts, spoke_w=None, max_reach=np.inf):
    """Point rings (bottom to top) of a flanked window: a ruled loft through them widens its top by
    p.flank_w over p.flank_depth.

    Each outline point moves along its outward normal (into the material), so the rings keep point
    correspondence and the flank rules straight across. Outlines are smooth (fillets / traced and
    smoothed), so the offset does not fold except at concave bends tighter than p.flank_w, where the
    offset is limited by the local bend radius.

    `spoke_w` is the material width to the nearest other window at each point. The offset is held
    to p.flank_share of it, so on a spoke narrower than two flanks the flanks from both sides meet in
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
    reach = np.minimum(min(p.flank_w, max_reach), .8 * radius)
    if spoke_w is not None:
        reach = np.minimum(reach, p.flank_share * np.asarray(spoke_w))
    from scipy.ndimage import gaussian_filter1d
    reach = gaussian_filter1d(reach, 2, mode='wrap')
    wide = _unfold(xy, normal, reach)
    z = lambda q: np.array([z_top(p, math.hypot(*v)) for v in q])
    D, c = p.flank_depth, p.face_chamfer
    if c > 0:
        # Chamfer at the flank crest: leave the flank c below the face, cross the face c + 1 farther out.
        on_flank = xy + (wide - xy) * ((D - c) / (D + 1))
        crest = _unfold(xy, normal, reach * D / (D + 1) + c + 1)
        top = [np.column_stack([on_flank, z(on_flank) - c]), np.column_stack([crest, z(crest) + 1.0])]
        wide = crest
    else:
        top = [np.column_stack([wide, z(wide) + 1.0])]
    return [np.column_stack([xy, np.full(len(xy), -p.width - 10.0)]),
            np.column_stack([xy, z(xy) - D]), *top,
            np.column_stack([wide, np.full(len(xy), 40.0)])]


def _crossing_segments(loop):
    """Indices of segments of a closed polyline that cross a non-adjacent segment."""
    a, b = loop, np.roll(loop, -1, axis=0)
    d = b - a
    rel = a[None, :, :] - a[:, None, :]                        # a_j - a_i
    cross = lambda u, v: u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]
    den = cross(d[:, None, :], d[None, :, :])
    with np.errstate(divide='ignore', invalid='ignore'):
        t = cross(rel, d[None, :, :]) / den
        u = cross(rel, d[:, None, :]) / den
    n = len(loop)
    idx = np.arange(n)
    near = np.abs((idx[:, None] - idx[None, :] + n // 2) % n - n // 2) <= 1
    hit = (t > 0) & (t < 1) & (u > 0) & (u < 1) & ~near
    return np.flatnonzero(hit.any(axis=1))


class FoldError(ValueError):
    """A flank offset that folds over itself however far its reach is pulled in."""


def _unfold(xy, normal, reach, rounds=60):
    """Widened flank loop without folds: points where the offset loop runs backwards or crosses
    itself are relaxed toward their neighbours until the loop is simple.

    A traced outline with small corner fillets turns faster than its samples resolve, so a
    per-point bend radius misses the fold at a concave corner (HF6-4 polygon trace, 2026-09-25:
    invalid loft). Shrinking the offset there left a notch in the spoke; relaxing the offset points
    instead rounds the flank's top edge over the corner, as a milled flank is, and keeps its width
    elsewhere. Point correspondence with `xy` is kept, so the flank still rules straight across.
    """
    from scipy.ndimage import gaussian_filter1d
    reach = np.array(reach, float)
    n = len(xy)
    # When relaxing alone cannot open a fold (a wide flank in a tight spot, HF6-4 flank_share .4,
    # 2026-09-26), the reach is narrowed smoothly around the fold and the relaxing starts again.
    for _attempt in range(6):
        wide = xy + normal * reach[:, None]
        for _ in range(rounds):
            seg0, seg1 = np.roll(xy, -1, axis=0) - xy, np.roll(wide, -1, axis=0) - wide
            bad = np.union1d(np.flatnonzero(np.sum(seg0 * seg1, axis=1) <= 0), _crossing_segments(wide))
            if not len(bad):
                return wide
            hit = np.unique(np.concatenate([(bad + k) % n for k in range(-3, 5)]))
            for _ in range(4):
                wide[hit] = .5 * wide[hit] + .25 * (wide[(hit - 1) % n] + wide[(hit + 1) % n])
        pull = np.zeros(n)
        pull[hit] = 1
        reach *= 1 - .3 * np.minimum(gaussian_filter1d(pull, 4, mode='wrap') * 4, 1)
    raise FoldError('flank offset still folds; lower flank_w')


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


def face_z(p, r):
    """Blank front at radius r: the dish inside the ring, the straight rise to the lip face beyond it."""
    if r <= p.ring_r:
        return z_top(p, r)
    return p.ring_z + (0.0 - p.ring_z) * min(1.0, (r - p.ring_r) / max(p.lip_face_r_in - p.ring_r, 1e-6))


def spoke_centrelines(p, res=.5):
    """Centrelines of spoke group 0's sections, from the traced windows (outline family).

    Every material point takes the label of its nearest window (the lip counts as one); where the
    label changes is the centreline of the spoke between those two windows (a Voronoi edge), and
    the distance there is the local half width. Returns [(points N x 2 ordered, half widths N)].
    """
    from PIL import Image, ImageDraw
    from scipy.ndimage import distance_transform_edt
    half = p.lip_r
    n = int(round(2 * half / res))
    img = Image.new('I', (n, n), 0)
    draw = ImageDraw.Draw(img)
    label = 0
    for g in range(p.spokes):
        for o in p.outlines:
            label += 1
            draw.polygon([((x + half) / res, (half - y) / res) for x, y in (polar(r, a + g * 360 / p.spokes) for r, a in o)],
                         fill=label)
    lab = np.asarray(img).copy()
    ys, xs = np.mgrid[0:n, 0:n]
    x, y = xs * res - half, half - ys * res
    r = np.hypot(x, y)
    lab[r > p.lip_face_r_in] = -1
    dist, (iy, ix) = distance_transform_edt(lab == 0, return_indices=True)
    near = lab[iy, ix]
    pair_a, pair_b = np.zeros_like(near), np.zeros_like(near)
    for dy, dx in ((0, 1), (1, 0)):
        other = np.roll(near, (-dy, -dx), axis=(0, 1))
        hit = (near != other) & (pair_a == 0)
        pair_a[hit], pair_b[hit] = np.minimum(near, other)[hit], np.maximum(near, other)[hit]
    on = (pair_a > 0) & (lab == 0) & (r > p.hub_r)
    pitch = 2 * math.pi / p.spokes
    lines = []
    for a, b in {(int(i), int(j)) for i, j in zip(pair_a[on], pair_b[on])}:
        sel = on & (pair_a == a) & (pair_b == b)
        pts = np.column_stack([x[sel], y[sel]])
        if len(pts) < 20 or not -pitch / 2 <= math.atan2(*pts.mean(0)[::-1]) < pitch / 2:
            continue
        centre = pts.mean(0)
        axis = np.linalg.svd(pts - centre, full_matrices=False)[2][0]
        u = (pts - centre) @ axis
        bins = np.arange(u.min(), u.max() + 2, 2.0)
        idx = np.digitize(u, bins)
        keep = [k for k in np.unique(idx) if (idx == k).sum() >= 2]
        line = np.array([pts[idx == k].mean(0) for k in keep])
        widths = np.array([dist[sel][idx == k].mean() * res for k in keep])
        lines.append((line, widths))
    return lines


def outline_groove_tools(p):
    """Centreline grooves for the outline family (see outline_groove_r)."""
    r0, r1 = p.outline_groove_r
    if p.family != 'outline' or r1 <= r0 or p.groove_w <= 0 or p.groove_depth <= 0:
        return []
    tools = []
    for line, half in spoke_centrelines(p):
        r = np.hypot(line[:, 0], line[:, 1])
        ok = (r >= r0) & (r <= r1) & (half >= p.groove_w / 2 + GROOVE_LAND)
        runs, start = [], None
        for i, flag in enumerate(list(ok) + [False]):
            if flag and start is None:
                start = i
            elif not flag and start is not None:
                runs.append((start, i)); start = None
        for i, j in runs:
            path = line[i:j]
            if len(path) < 2 or np.sum(np.linalg.norm(np.diff(path, axis=0), axis=1)) < 20:
                continue
            def section(fade, at, t, w=p.groove_w / 2):
                low = []
                for s_ in (-w, w):
                    x, y = at(s_)
                    z = face_z(p, math.hypot(x, y))
                    low.append((x, y, z + .5 - (p.groove_depth + .5) * fade))
                return low + [(x, y, z + 30) for x, y, z in low[::-1]]
            tools.append(segment_loft(p, path, section, n=max(13, len(path) // 2)))
    return tools


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
    pts = window_envelope_profile(p)
    return cq.Workplane('XZ').polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0)).val()


def window_envelope_profile(p):
    """(r, z) outline of the window envelope, revolved about the wheel axis."""
    # Pocket floor keeps POCKET_SKIN above the web's back at the ring: with the spoke ends lowered
    # (ring_z < 0) a 60 mm pocket cut through to the barrel (HF6-4 v3, 2026-09-25).
    floor = max(-p.window_pocket_depth, z_back(p, p.ring_r) + POCKET_SKIN)
    ring = p.ring_r - 2
    # Never past the lip face: a flank widens the outline outward, and with the spoke ends below
    # the lip (ring_z < 0) it cut the lip flange away (HF6-4, 2026-09-25).
    outer = p.lip_face_r_in - 1
    # Past the barrel's inner wall the pocket runs under the lip flange, which is only ~14 mm thick
    # outside the barrel: a floor deeper than the flange opened a ring of daylight behind the lip
    # (HF6-4 v4, 2026-09-25). A flat shelf there read as a lit ring inside the lip; the floor now
    # climbs steeply from the barrel wall to just under the lip face (dark, as the official deep
    # pockets look), held POCKET_SKIN above the flange's back wherever the flange is thin.
    shelf_r = min(max(p.barrel_inner_r, ring), outer)
    lip_back = -min(14.0, p.lip_r - p.barrel_outer_r)
    back = lambda r: float(np.interp(r, [p.barrel_outer_r, p.barrel_outer_r + 2, p.lip_r],
                                     [lip_back - 26, lip_back - 12, lip_back]))
    rs = np.linspace(shelf_r, outer, 12)
    top = -2.0
    climb = [(float(r), max(floor + (top - floor) * (r - shelf_r) / max(outer - shelf_r, 1e-6),
                            back(r) + POCKET_SKIN if r > p.barrel_outer_r else -1e9)) for r in rs]
    pts = [(0, -p.width - 30), (ring, -p.width - 30), (ring, floor), (shelf_r, floor), *climb, (outer, 80), (0, 80)]
    return [q for i, q in enumerate(pts) if i == 0 or np.hypot(q[0] - pts[i - 1][0], q[1] - pts[i - 1][1]) > 1e-6]


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
    trimmed = []
    for t, pts in zip(tools if isinstance(tools, list) else [tools], outlines):
        out = _trim(t, envelope)
        if not out.Solids() and isinstance(tools, list):
            # The flank limited by the neighbouring windows made one HF6-5 window untrimmable; the
            # same window flanked on its own trimmed fine (2026-09-26).
            alone = windows(p, [pts])[0]
            out = _trim(alone, envelope)
        trimmed.append(out)
    trimmed = _borrow_trimmed(trimmed, outlines)
    bevel = min(SMALL_BEVEL_MAX, max(p.face_chamfer, 2.0)) if p.face_chamfer > 0 else 0.0
    for t, pts in zip(trimmed, outlines):
        # Straight walls with a bevel when the flanked cut fails in the body (HF-5, 2026-09-26).
        t.fallback = lambda pts=pts: _prism_window(p, pts, chamfer=bevel).intersect(envelope)
    return trimmed


def _trim(tool, envelope):
    """tool & envelope, retried (fuzzy, turned, operands swapped, envelope seam moved) when OCC returns
    nothing (every copy of one HF6-5 window did, 2026-09-26); still empty is left for _borrow_trimmed."""
    z = ((0, 0, 0), (0, 0, 1))
    for attempt in (lambda: tool.intersect(envelope), lambda: tool.intersect(envelope, tol=1e-3),
                    lambda: tool.rotate(*z, .01).intersect(envelope), lambda: envelope.intersect(tool),
                    lambda: envelope.rotate(*z, 7.0).intersect(tool)):     # the revolve's seam elsewhere
        out = attempt()
        if out.Solids():
            return out
    return out


def _borrow_trimmed(trimmed, outlines):
    """Fill a window whose trim came out empty with the same window of another spoke group, turned
    back. One window of HF6-5 trimmed to nothing whatever was tried (flanked or straight-walled,
    fuzzy, turned) while its copies in the other groups trimmed after a retry (2026-09-26)."""
    def key(pts):
        xy = np.asarray(pts, float)
        c = xy.mean(axis=0)
        area = .5 * abs(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))
        return math.hypot(*c), math.degrees(math.atan2(c[1], c[0])), area
    keys = [key(pts) for pts in outlines]
    out = list(trimmed)
    for i, tool in enumerate(trimmed):
        if tool.Solids():
            continue
        r, a, area = keys[i]
        mine = np.asarray(outlines[i], float)

        def turned_onto_mine(j):          # a rotation of window i, not its mirror image in the group
            t = math.radians(a - keys[j][1])
            xy = np.asarray(outlines[j], float) @ np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])
            return np.min(np.linalg.norm(mine[:, None] - xy[None], axis=2), axis=1).max() < 1.0
        twins = [j for j, (rj, _, aj) in enumerate(keys) if j != i and trimmed[j].Solids()
                 and abs(rj - r) < 1.0 and abs(aj - area) < .01 * area and turned_onto_mine(j)]
        if not twins:
            raise RuntimeError('window tool: trimming to the window envelope gives nothing (OCC boolean failure)')
        out[i] = trimmed[twins[0]].rotate((0, 0, 0), (0, 0, 1), a - keys[twins[0]][1])
    return out


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


def _valley_radii(p):
    """(r_in, r_out) of a hub valley's top outline. An unset r_in moves out from bore + 6 until the
    floor (arms widened by the draft run) leaves room for its fillets; None when it never does."""
    run = p.hub_valley_depth * math.tan(math.radians(p.hub_valley_draft_deg))
    mid, fillet = 180 / p.spokes, 3.0
    r_in, r_out = p.hub_valley_r
    r_out = r_out or min(p.pcd / 2 + max(p.lug_pocket_d, p.seat_d) / 2 + 6, p.window_r_in - 3)
    if r_in:
        return r_in, r_out
    half = p.hub_arm_w / 2 + run

    def fits(r):
        ri = r + run
        return ri > half and ri * math.radians(2 * (mid - math.degrees(math.asin(half / ri)))) >= 2 * fillet + 2

    for r in np.arange(p.center_bore_r + 6, r_out - 2 * run - 2 * fillet - 2, .5):
        if fits(r):
            return float(r), r_out
    return None


def hub_valley_tools(p):
    """One valley between spoke 0 and spoke 1 (rotated round by build): flat floor at hub_z - depth,
    drafted walls up to the face, vertical above.

    Floor and top outlines are built alike from lines, concentric arcs and corner fillets, and joined
    by a ruled loft, so the walls are planes, cones and simple ruled patches. Spline-ring walls
    (sampled offsets) made lug-seat cuts fail once the face had a hub crease (2026-09-26), and a
    drafted prism of the filleted outline failed outright (degenerate offset arcs).
    """
    if p.hub_valley_depth <= 0:
        return []
    run = p.hub_valley_depth * math.tan(math.radians(p.hub_valley_draft_deg))
    mid = 180 / p.spokes
    radii = _valley_radii(p)
    if radii is None:
        raise ValueError(f'hub valley has no floor: arm {p.hub_arm_w} mm, draft run {run:.1f} mm')
    r_in, r_out = radii
    fillet = 3.0

    def outline(inset, z):
        """Top outline pulled in by `inset` (arm edges, both arcs); fillets shrink with it."""
        half, ri, ro = p.hub_arm_w / 2 + inset, r_in + inset, r_out - inset
        edge = lambda r: math.degrees(math.asin(half / r))
        if ri <= half or ri * math.radians(2 * (mid - edge(ri))) < 2 * fillet + 2 or ro < ri + 2 * fillet + 2:
            raise ValueError(f'hub valley has no floor: radii {r_in:.1f}-{r_out:.1f}, arm {p.hub_arm_w} mm, '
                             f'draft run {run:.1f} mm')
        pt = lambda r, deg: cq.Vector(r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg)), z)
        a_in, a_out = edge(ri), edge(ro)
        wire = cq.Wire.assembleEdges([
            cq.Edge.makeLine(pt(ri, a_in), pt(ro, a_out)),
            cq.Edge.makeThreePointArc(pt(ro, a_out), pt(ro, mid), pt(ro, 2 * mid - a_out)),
            cq.Edge.makeLine(pt(ro, 2 * mid - a_out), pt(ri, 2 * mid - a_in)),
            cq.Edge.makeThreePointArc(pt(ri, 2 * mid - a_in), pt(ri, mid), pt(ri, a_in))])
        return cq.Face.makeFromWires(wire).fillet2D(fillet + (run - inset), cq.Face.makeFromWires(wire).Vertices()).outerWire()

    # Flat top ring at the highest face point on the outline: a top ring following the rising dish
    # gave a loft that OCC booleans got wrong (cut above the face, nothing below).
    top_z = max(z_top(p, r) for r in np.linspace(r_in, r_out, 12))
    wires = [outline(run, p.hub_z - p.hub_valley_depth), outline(0.0, top_z), outline(0.0, top_z + 20)]
    kinds = [[e.geomType() for e in w.Edges()] for w in wires]
    if any(k != kinds[0] for k in kinds):
        raise ValueError(f'hub valley outlines differ in structure: {kinds}')
    return [cq.Solid.makeLoft(wires, ruled=True)]


PAD_MIN_HALF = 8.0   # skeleton lines narrower than this (half width, mm) are side ribs: lowered, not pads


def spoke_pad_tools(p, trim=True):
    """Pockets that leave a pad standing along every spoke's skeleton (outline family).

    The skeleton is the traced spokes' centrelines (stem and arms, at least PAD_MIN_HALF half wide),
    so the pad runs from the root through the fork into both arms as one Y. Everything farther than
    min(spoke_pad_w / 2, spoke_pad_share x half width) from it, inside spoke_pad_r, is lowered
    spoke_pad_depth under the dish with walls drafted spoke_pad_draft_deg. One tool per pocket
    of spoke group 0 (rotated round by build); pockets run into the windows, which are air.
    """
    from PIL import Image, ImageDraw
    from scipy.ndimage import distance_transform_edt, label as components
    from .forged_photo import _polygon_loop
    from .window_fit import _cell_boundary_loops
    r0, r1 = p.spoke_pad_r
    if p.family != 'outline' or p.spoke_pad_w <= 0 or p.spoke_pad_depth <= 0 or r1 <= r0:
        return []
    pads = [(line, hw) for line, hw in spoke_centrelines(p) if np.median(hw) >= PAD_MIN_HALF]
    res, half = .5, r1 + 12
    n = int(round(2 * half / res))
    keep = np.zeros((n, n))                          # skeleton pixels carry their pad half width
    img = Image.new('F', (n, n), 0.0)
    draw = ImageDraw.Draw(img)
    for g in range(p.spokes):
        c, s_ = math.cos(2 * math.pi * g / p.spokes), math.sin(2 * math.pi * g / p.spokes)
        for line, hw in pads:
            pts = [((x * c - y * s_ + half) / res, (half - (x * s_ + y * c)) / res) for x, y in line]
            for (pa, pb), w in zip(zip(pts[:-1], pts[1:]), hw[:-1]):
                draw.line([pa, pb], fill=float(min(p.spoke_pad_w / 2, p.spoke_pad_share * w)), width=1)
    keep = np.asarray(img)
    dist, (iy, ix) = distance_transform_edt(keep <= 0, return_indices=True)
    ys, xs = np.mgrid[0:n, 0:n]
    x, y = xs * res - half, half - ys * res
    r = np.hypot(x, y)
    low = (dist * res > keep[iy, ix]) & (r >= r0) & (r <= r1)
    regions, count = components(low)
    pitch = 2 * math.pi / p.spokes
    run = p.spoke_pad_depth * math.tan(math.radians(p.spoke_pad_draft_deg))
    floor = _offset_dish(p, p.spoke_pad_depth) if trim else None
    tools = []
    for k in range(1, count + 1):
        region = regions == k
        if region.sum() * res * res < 100:
            continue
        cx, cy = x[region].mean(), y[region].mean()
        if not 0 <= math.atan2(cy, cx) % (2 * math.pi) < pitch:
            continue
        loop = max(_cell_boundary_loops(region), key=len)
        pts = np.array([(c_ * res - half, half - r_ * res) for r_, c_ in loop])
        pts = _polygon_loop(pts, corner_r=run + 1.5)
        if not trim:                       # the mesh build lofts the rings and trims them itself
            tools.append(_pad_rings(p, pts, run))
            continue
        tool = _pad_pocket(p, pts, run).intersect(floor)
        if tool.Solids():                  # a pocket wholly above the offset dish cuts nothing (HF6-5)
            tools.append(tool)
    return tools


def offset_dish_profile(p, depth):
    """(r, z) of the dish lowered by `depth`, from the axis to past the lip."""
    rs = np.linspace(0, p.lip_r + 5, 60)
    return [(float(r), face_z(p, max(float(r), p.hub_r)) - depth) for r in rs]


def _offset_dish(p, depth):
    """Everything above the dish lowered by `depth` (r up to the lip): the floor of a pad pocket."""
    prof = offset_dish_profile(p, depth)
    wp = cq.Workplane('XZ').moveTo(0, 80).lineTo(0, prof[0][1]).spline(prof[1:], includeCurrent=True)
    return wp.lineTo(p.lip_r + 5, 80).close().revolve(360, (0, 0, 0), (0, 1, 0)).val()


def _pad_pocket(p, pts, run, samples=220):
    """Drafted pocket through the loop `pts` (see _pad_rings)."""
    return _loft_rings(_pad_rings(p, pts, run, samples))


def _pad_rings(p, pts, run, samples=220):
    """Drafted pocket through the loop `pts` (its top edge on the face): the wall runs from `run`
    inside the loop, below the floor, out through the face and on 3 mm above it (a wall edge on
    the face made cuts fail), then straight up. The floor comes from the offset-dish intersection."""
    closed = np.vstack([pts, pts[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    u = np.linspace(0, cum[-1], samples, endpoint=False)
    xy = np.column_stack([np.interp(u, cum, closed[:, 0]), np.interp(u, cum, closed[:, 1])])
    tangent = np.roll(xy, -1, axis=0) - np.roll(xy, 1, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    normal = np.column_stack([tangent[:, 1], -tangent[:, 0]])
    if .5 * np.sum(xy[:, 0] * np.roll(xy, -1, axis=0)[:, 1] - np.roll(xy, -1, axis=0)[:, 0] * xy[:, 1]) < 0:
        normal = -normal                                   # outward from the pocket
    d, c = p.spoke_pad_depth, p.face_chamfer
    inner = _unfold(xy, -normal, np.full(len(xy), run * (d + 2) / d))
    face = lambda q: np.array([face_z(p, float(np.hypot(*v))) for v in q])
    fz = face(xy)
    if c > 0:                                  # chamfer: leave the wall c below the face, cross it c + 1 out
        on_wall = xy - normal * (run * c / d)
        outer = xy + normal * (c + 1.0)
        top = [np.column_stack([on_wall, fz - c]), np.column_stack([outer, fz + 1])]
    else:
        outer = xy + normal * (run * 3 / d)
        top = [np.column_stack([outer, fz + 3])]
    # End rings flat, so the loft closes with planar caps (sloped end rings left it open, invalid).
    return [np.column_stack([inner, np.full(len(xy), fz.min() - d - 12)]), np.column_stack([inner, fz - d - 2]),
            *top, np.column_stack([outer, np.full(len(xy), fz.max() + 30)])]


def lug_tools(p):
    """One tool per lug: bolt hole, seat counterbore and pocket fused. Cut one after another, the
    seat of one HF6-5 lug gave an invalid solid whatever its size or a small turn; fused, it cut
    clean (2026-09-26)."""
    tools = []
    for i in range(p.bolts):
        x, y = polar(p.pcd / 2, 180 / p.spokes + i * 360 / p.bolts)
        parts = [cq.Workplane('XY', origin=(x, y, -p.width)).circle(p.bolt_d / 2).extrude(p.width + 10).val()]
        seat_z = p.hub_z - p.seat_depth
        parts.append(cq.Workplane('XY', origin=(x, y, seat_z)).circle(p.seat_d / 2).extrude(40).val())
        if p.lug_pocket_d > p.seat_d:
            wp = cq.Workplane('XY', origin=(x, y, p.hub_z - p.lug_pocket_depth))
            if p.lug_pocket_sides >= 3:
                angle = math.degrees(math.atan2(y, x))
                corners = [polar(p.lug_pocket_d / 2, angle + 180 + k * 360 / p.lug_pocket_sides) for k in range(p.lug_pocket_sides)]
                sketch = cq.Sketch().polygon(corners + [corners[0]]).vertices().fillet(3)
                parts.append(wp.placeSketch(sketch).extrude(40).val())
            else:
                parts.append(wp.circle(p.lug_pocket_d / 2).extrude(40).val())
        tools.append(parts[0].fuse(*parts[1:]).clean())
    return tools


def _wedge(p, a0, a1):
    """Prism over the sector a0..a1 (deg) reaching past the lip, through the whole width."""
    R = p.lip_r + 20
    pt = lambda a: cq.Vector(R * math.cos(math.radians(a)), R * math.sin(math.radians(a)), -p.width - 60)
    o = cq.Vector(0, 0, -p.width - 60)
    wire = cq.Wire.assembleEdges([cq.Edge.makeLine(o, pt(a0)), cq.Edge.makeThreePointArc(pt(a0), pt((a0 + a1) / 2), pt(a1)),
                                  cq.Edge.makeLine(pt(a1), o)])
    return cq.Solid.extrudeLinear(cq.Face.makeFromWires(wire), cq.Vector(0, 0, p.width + 160))


def _overlaps(a, b, pad=1.0):
    return not (a.xmax < b.xmin - pad or a.xmin > b.xmax + pad or a.ymax < b.ymin - pad or a.ymin > b.ymax + pad)


def _robust_cut(body, tool, name):
    """body - tool, retried when OCC returns an invalid solid: a 0.01 deg turn of the tool about the
    wheel axis (far below machining tolerance), then a fuzzy boolean. One slot-pocket cut of HF6-4
    came out invalid while its mirror image cut clean; both retries fixed it (2026-09-26)."""
    def tidy(shape):
        # Zero-volume slivers (a window meeting the lip-pocket climb, 2026-09-26) and free faces or
        # shells left beside the solid (HF6-1 stem slots) are dropped; a real loose piece (a window
        # plug) is kept and caught by the caller.
        solids = shape.Solids()
        keep = [x for x in solids if x.Volume() > 1.0] if len(solids) > 1 else solids
        if not keep:
            return shape
        loose = len(shape.Faces()) != sum(len(x.Faces()) for x in solids)
        if len(keep) < len(solids) or loose:
            shape = keep[0] if len(keep) == 1 else cq.Compound.makeCompound(keep)
        return shape
    cut = tidy(body.cut(tool))
    if cut.isValid():
        return cut
    for retry in (lambda: body.cut(tool.rotate((0, 0, 0), (0, 0, 1), .01)), lambda: body.cut(tool, tol=1e-3)):
        cut = tidy(retry())
        if cut.isValid():
            return cut
    # Last: ShapeFix on the plain cut, kept only when it changes no volume (it repaired an HF6-5 lug
    # seat cut that the retries above could not, 2026-09-26).
    from OCP.ShapeFix import ShapeFix_Shape
    cut = tidy(body.cut(tool))
    fix = ShapeFix_Shape(cut.wrapped)
    fix.Perform()
    fixed = cq.Shape.cast(fix.Shape())
    if fixed.isValid() and len(fixed.Solids()) == len(cut.Solids()) and abs(fixed.Volume() - cut.Volume()) < 1.0:
        return fixed
    raise RuntimeError(f'{name}: cut gives an invalid solid even after retries (OCC boolean failure)')


def _clean_measured(body):
    """(body, volume) with clean() applied when the cleaned body is valid and still integrates; after
    the HF6-5 lug cuts it was valid but the volume integration failed (2026-09-26)."""
    from .mass_properties import VolumeMeasurementError
    cleaned = body.clean()
    if cleaned.isValid() and len(cleaned.Solids()) == len(body.Solids()):
        try:
            return cleaned, volume(cleaned)
        except VolumeMeasurementError:
            pass
    return body, volume(body)


def _build_sector(p):
    """Outline-family build on one spoke sector, patterned round (2026-09-26).

    The sector (spoke 0 +- half a pitch) is cut with the tools of spoke groups -1, 0 and +1 that
    reach it, then rotated copies are glued into the wheel. Features that do not repeat with the
    spokes (lug holes, a lip-pocket count that is not a multiple of the spoke count) are cut on the
    whole wheel afterwards. Whole-wheel builds took 15-40 min and every extra group multiplied the
    chances of an OCC boolean failure; a sector has a sixth of the faces.
    """
    stock = blank(p)
    pitch = 360 / p.spokes
    # Sector edges half a pitch off the spokes (through the big windows between groups). Edges on
    # the spoke axes (to take the lug holes into the sector) cut through pads and fork windows, and
    # the glued wheel came out invalid (2026-09-26); lugs are cut on the whole wheel.
    wedge = _wedge(p, -pitch / 2, pitch / 2)
    body = stock.intersect(wedge).Solids()[0]
    reach = wedge.BoundingBox()
    stages = []

    def apply(name, tools, rotate=False, whole=False):
        nonlocal body
        if not tools:
            return
        start, before = time.time(), volume(body)
        limit = body.BoundingBox()
        if rotate:
            tools = [t.rotate((0, 0, 0), (0, 0, 1), k * pitch) for k in (-1, 0, 1) for t in tools]
        if not whole:
            tools = [t for t in tools if _overlaps(t.BoundingBox(), reach)]
        solids = len(body.Solids())
        start_body, fallbacks = body, 0

        def guarded(shape, tool):
            out = _robust_cut(shape, tool, name)
            bb = out.BoundingBox()           # a cut only removes material (see build)
            if (bb.zmax > limit.zmax + .5 or bb.zmin < limit.zmin - .5 or bb.xmax > limit.xmax + .5
                    or bb.ymax > limit.ymax + .5 or bb.xmin < limit.xmin - .5 or bb.ymin < limit.ymin - .5):
                raise RuntimeError(f'{name}: a cut added material outside the part (OCC boolean failure)')
            if len(out.Solids()) > solids:
                raise RuntimeError(f'{name}: a cut left a loose piece (OCC boolean failure)')
            return out

        def cut_all(order):
            """Cuts in `order`; a failing tool waits for the others, then gets its fallback."""
            nonlocal fallbacks
            shape, waiting = start_body, []
            for tool in order:
                try:
                    shape = guarded(shape, tool)
                except RuntimeError:
                    waiting.append(tool)
            for tool in waiting:
                try:
                    shape = guarded(shape, tool)
                except RuntimeError:
                    if getattr(tool, 'fallback', None) is None:
                        raise
                    shape = guarded(shape, tool.fallback())
                    fallbacks += 1
            return shape

        # Sequential cuts leave a body some later cut fails on, depending on the order: HF6-5's
        # windows failed four in a row in trace order and all cut small-first (2026-09-26).
        try:
            body = cut_all(tools)
        except RuntimeError:
            fallbacks = 0
            body = cut_all(sorted(tools, key=lambda t: t.Volume()))
        body, after = _clean_measured(body)
        stages.append({'op': name, 'removed_mm3': round(before - after, 1), 'valid': body.isValid(),
                       **({'fallbacks': fallbacks} if fallbacks else {}),
                       'solids': len(body.Solids()), 'seconds': round(time.time() - start, 1)})

    apply('face_facets', facet_cutters(p), rotate=True)
    apply('through_windows', outline_window_tools(p))
    apply('stem_slots', slot_tools(p), rotate=True)
    apply('window_pockets', window_pocket_tools(p))
    apply('spoke_grooves', groove_cutters(p), rotate=True)
    apply('spoke_grooves_outline', outline_groove_tools(p), rotate=True)
    apply('back_pockets', back_pocket_cutters(p), rotate=True)
    periodic_lip = p.lip_pockets and p.lip_pockets % p.spokes == 0
    if periodic_lip:
        apply('lip_pockets', lip_pockets(p))
    apply('spoke_pads', spoke_pad_tools(p), rotate=True)
    apply('hub_valleys', hub_valley_tools(p), rotate=True)

    start = time.time()
    sector = body
    if os.environ.get('WHEELCAM_SECTOR_DUMP'):
        cq.exporters.export(sector, os.environ['WHEELCAM_SECTOR_DUMP'])
    body = sector.fuse(*[sector.rotate((0, 0, 0), (0, 0, 1), k * pitch) for k in range(1, p.spokes)], glue=True)
    # clean() (merging the split faces along the seams) made the glued wheel invalid (HF6-4, 2026-09-26);
    # the seams stay as edges across smooth faces, invisible and harmless for CAM.
    cleaned = body.clean()
    if cleaned.isValid() and len(cleaned.Solids()) == 1:
        body = cleaned
    if len(body.Solids()) != 1 or not body.isValid():
        raise RuntimeError(f'sector pattern: {len(body.Solids())} solids, valid {body.isValid()} (OCC glue failure)')
    stages.append({'op': 'sector_pattern', 'removed_mm3': 0.0, 'valid': True, 'solids': 1,
                   'seconds': round(time.time() - start, 1)})
    reach = body.BoundingBox()
    if p.lip_pockets and not periodic_lip:
        apply('lip_pockets', lip_pockets(p), whole=True)
    apply('lug_holes_and_seats', lug_tools(p), whole=True)
    return stock, body, stages


def build(p, sector=None):
    """Blank and cut part. `sector` (default: outline family without a face surface) builds one
    spoke sector and patterns it; otherwise every tool cuts the whole wheel."""
    if sector is None:
        sector = p.family == 'outline' and p.face_crown_w <= 0
    if sector:
        return _build_sector(p)
    body = blank(p)
    stock = body
    limit = stock.BoundingBox()
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
                # A cut can only remove material. OCC once returned a 'valid' solid with the tool
                # merged in (fork triangles as posts standing 40 mm proud, 2026-09-25): stop there.
                bb = body.BoundingBox()
                if (bb.zmax > limit.zmax + .5 or bb.zmin < limit.zmin - .5 or bb.xmax > limit.xmax + .5
                        or bb.ymax > limit.ymax + .5 or bb.xmin < limit.xmin - .5 or bb.ymin < limit.ymin - .5):
                    raise RuntimeError(f'{name}: a cut added material outside the blank (OCC boolean failure)')
                # ... or left the window plug behind as a loose solid (flank lofts, synthetic dish, 2026-09-26).
                if len(body.Solids()) > len(stock.Solids()):
                    raise RuntimeError(f'{name}: a cut left a loose piece (OCC boolean failure)')
        body, after = _clean_measured(body)
        stages.append({'op': name, 'removed_mm3': round(before - after, 1),
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
    apply('spoke_grooves_outline', outline_groove_tools(p), rotate=True)
    apply('back_pockets', back_pocket_cutters(p), rotate=True)
    apply('lip_pockets', lip_pockets(p))
    apply('spoke_pads', spoke_pad_tools(p), rotate=True)
    apply('hub_valleys', hub_valley_tools(p), rotate=True)
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
    for key in ('lip_pocket_r', 'groove_offsets', 'hub_valley_r', 'outline_groove_r', 'spoke_pad_r'):
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
