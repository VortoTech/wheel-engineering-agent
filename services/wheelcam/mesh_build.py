"""Mesh build of an outline-family forged wheel (manifold3d), from the same tool geometry as forged_blank.

The B-Rep build cuts dozens of lofted tools one after another, and OCC failed on most wheels of the
ten-wheel eval set (2026-09-26): a valid body that the next cut turned invalid, depending on the order.
Booleans on closed triangle meshes (manifold3d) do not fail that way and take a fraction of a second,
so design, preview and the eval use this build; the STEP for manufacturing stays with forged_blank.

Tools are the point rings forged_blank computes (window_rings), lofted into triangle strips instead of
splines, and the revolve profiles of the blank and the window envelope.
"""
import math

import numpy as np

from .forged_blank import (PAD_MIN_HALF, POCKET_SKIN, face_z, hub_valley_tools, offset_dish_profile,
                           outline_groove_tools, recipe_from_dict, spoke_centrelines, spoke_pad_tools,
                           window_envelope_profile, window_rings, z_back, z_top)

SEGMENTS = 720               # revolve and hole resolution: 0.5 deg, ~2.8 mm at a 22" lip
DENSITY_6061 = 2.70e-6       # kg / mm3


def _m3():
    import manifold3d
    return manifold3d


def revolve(profile, segments=SEGMENTS):
    """Solid of revolution of an (r, z) outline about the Z axis."""
    m3 = _m3()
    pts = np.asarray(profile, float)
    if _area(pts) < 0:
        pts = pts[::-1]
    return m3.CrossSection([pts], m3.FillRule.NonZero).revolve(segments)


def _area(xy):
    return .5 * float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))


def loft(rings):
    """Closed mesh through point rings (bottom to top, same point count, flat end rings)."""
    m3 = _m3()
    rings = [np.asarray(r, float) for r in rings]
    if _area(rings[0][:, :2]) < 0:                 # counter-clockwise from above: side faces point out
        rings = [r[::-1] for r in rings]
    n, k = len(rings[0]), len(rings)
    verts = np.concatenate(rings)
    i = np.arange(n)
    j = (i + 1) % n
    sides = []
    for level in range(k - 1):
        a, b = level * n + i, level * n + j
        c, d = (level + 1) * n + j, (level + 1) * n + i
        sides += [np.column_stack([a, b, c]), np.column_stack([a, c, d])]
    cap = np.asarray(m3.triangulate([rings[0][:, :2]]), np.int64)
    top = np.asarray(m3.triangulate([rings[-1][:, :2]]), np.int64) + (k - 1) * n
    tris = np.concatenate(sides + [cap[:, ::-1], top])
    return m3.Manifold(m3.Mesh(vert_properties=verts.astype(np.float32), tri_verts=tris.astype(np.uint32)))


def blank_profile(p, samples=80):
    """(r, z) outline of the revolved blank, as forged_blank.blank draws it (dish splines sampled)."""
    creased = p.hub_crease_r > p.hub_r
    start = p.hub_crease_r if creased else p.hub_r
    lip_back = -min(14.0, p.lip_r - p.barrel_outer_r)
    pts = [(p.center_bore_r, p.hub_z), (p.hub_r, p.hub_z)]
    if creased:
        pts.append((p.hub_crease_r, p.hub_crease_z))
    pts += [(float(r), float(z_top(p, r))) for r in np.linspace(start, p.ring_r, samples)[1:]]
    pts += [(p.lip_face_r_in, 0.0), (p.lip_r, 0.0), (p.lip_r, lip_back), (p.barrel_outer_r + 2, lip_back - 12),
            (p.barrel_outer_r, lip_back - 26), (p.barrel_outer_r, -p.width + 25), (p.lip_r - 2, -p.width + 12),
            (p.lip_r - 2, -p.width), (p.barrel_inner_r, -p.width), (p.barrel_inner_r, z_back(p, p.ring_r) - 3),
            (p.ring_r + 4, z_back(p, p.ring_r))]
    pts += [(float(r), float(z_back(p, r))) for r in np.linspace(p.ring_r, p.hub_r, samples // 2)]
    pts.append((p.center_bore_r, float(z_back(p, p.hub_r))))
    return pts


def to_manifold(shape, tolerance=.05, angular=.1):
    """Closed mesh of one B-Rep tool. Building a single tool never failed in OCC; the booleans between
    tools and the part did, so tools are tessellated and every boolean happens on meshes."""
    import trimesh
    m3 = _m3()
    verts, tris = shape.tessellate(tolerance, angular)
    tm = trimesh.Trimesh(np.array([v.toTuple() for v in verts]), np.array(tris), process=True)
    tm.merge_vertices()
    out = m3.Manifold(m3.Mesh(vert_properties=np.asarray(tm.vertices, np.float32),
                              tri_verts=np.asarray(tm.faces, np.uint32)))
    if out.status() != m3.Error.NoError or out.is_empty():
        raise ValueError(f'mesh build: tool did not tessellate to a closed mesh ({out.status()})')
    return out


def _round(tools, p):
    """Tools of spoke group 0 repeated for every group."""
    pitch = 360 / p.spokes
    return [t.rotate([0, 0, k * pitch]) for k in range(p.spokes) for t in tools]


def _cylinder(r, z0, z1, x=0.0, y=0.0, segments=96):
    m3 = _m3()
    return m3.Manifold.cylinder(z1 - z0, r, r, segments).translate([x, y, z0])


def lug_tools(p):
    """Bolt hole, seat counterbore and (hex) lug pocket per lug, as forged_blank.lug_tools."""
    m3 = _m3()
    tools = []
    for i in range(p.bolts):
        a = math.radians(180 / p.spokes + i * 360 / p.bolts)
        x, y = p.pcd / 2 * math.cos(a), p.pcd / 2 * math.sin(a)
        tools.append(_cylinder(p.bolt_d / 2, -p.width - 10, 10, x, y))
        seat_z = p.hub_z - p.seat_depth
        tools.append(_cylinder(p.seat_d / 2, seat_z, seat_z + 40, x, y))
        if p.lug_pocket_d > p.seat_d:
            z0 = p.hub_z - p.lug_pocket_depth
            if p.lug_pocket_sides >= 3:
                corners = [(p.lug_pocket_d / 2 * math.cos(a + math.pi + 2 * math.pi * k / p.lug_pocket_sides),
                            p.lug_pocket_d / 2 * math.sin(a + math.pi + 2 * math.pi * k / p.lug_pocket_sides))
                           for k in range(p.lug_pocket_sides)]
                cs = m3.CrossSection([np.asarray(corners)]).offset(-3, m3.JoinType.Round).offset(3, m3.JoinType.Round)
                tools.append(cs.extrude(40).translate([x, y, z0]))
            else:
                tools.append(_cylinder(p.lug_pocket_d / 2, z0, z0 + 40, x, y))
    return tools


def outlines(p, samples=200):
    """Every window outline of the wheel (all spoke groups), sampled as forged_blank samples them.

    Arc-length sampling of the splines is the slow part (38 s of a 40 s HF6-4 build): group 0 is
    sampled and turned round numerically, which is exact (the spline through turned points is the
    turned spline)."""
    import cadquery as cq
    group = []
    for outline in p.outlines:
        pts = [cq.Vector(r * math.cos(math.radians(a)), r * math.sin(math.radians(a)), 0) for r, a in outline]
        edge = cq.Edge.makeSpline(pts, periodic=True)
        group.append(np.array([edge.positionAt(i / samples).toTuple()[:2] for i in range(samples)]))
    out = []
    for k in range(p.spokes):
        t = 2 * math.pi * k / p.spokes
        rot = np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])
        out += [(xy @ rot).tolist() for xy in group]
    return out


RIDGE_STEP = 1.5      # skeleton step of the ridge tents, mm
RIDGE_SMOOTH_MM = 8.0  # smoothing of the measured spoke half width along the skeleton (steps showed as facets)
RIDGE_MIN_RUN = 7.0   # least slope run of a ridge side (a 10 mm drop at 35 deg from vertical), mm


def ridge_tools(p):
    """Spoke pads as a ridge: a flat strip along every spoke's skeleton whose sides slope straight
    down to the spoke edge, spoke_pad_depth below the dish there (the official HF6-4 spine). The
    pocket pads left a flat shelf and a stepped wall beside the strip instead (2026-09-27).

    Returns (band, tents): the zone to lower (spoke_pad_r, drafted ends, down to the lowered dish)
    and the roofs that stay, one convex hull per skeleton step of group 0."""
    m3 = _m3()
    r0, r1 = p.spoke_pad_r
    d = p.spoke_pad_depth
    if p.spoke_pad_w <= 0 or d <= 0 or r1 - r0 < 2 * (d + 10):
        return None, []
    up = 20.0                                                   # at most this far above the face
    tents = []
    for line, hw in spoke_centrelines(p):
        if np.median(hw) < PAD_MIN_HALF:
            continue                                            # side ribs are lowered whole
        n = max(2, int(np.sum(np.linalg.norm(np.diff(line, axis=0), axis=1)) / RIDGE_STEP) + 1)
        cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
        u = np.linspace(0, cum[-1], n)
        pts = np.column_stack([np.interp(u, cum, line[:, 0]), np.interp(u, cum, line[:, 1])])
        from scipy.ndimage import gaussian_filter1d
        half = gaussian_filter1d(np.interp(u, cum, hw), RIDGE_SMOOTH_MM / RIDGE_STEP, mode='nearest')
        tangent = np.gradient(pts, axis=0)
        tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
        normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
        section = []
        for q, t, nrm, h in zip(pts, tangent, normal, half):
            a = min(p.spoke_pad_w / 2, p.spoke_pad_share * h)
            run = max(h - a, RIDGE_MIN_RUN)
            z = face_z(p, float(np.hypot(*q)))
            # The slope runs on above the face, but only to near the skeleton: carried the full
            # `up` it crossed over and the hull covered the whole spoke (nothing was cut).
            rise = min(up, .9 * a * d / run)
            ring = []
            for side in (1, -1):
                for off, dz in ((a - rise * run / d, rise), (a + run * (d + 2) / d, -d - 2)):
                    for along in (-.75, .75):                   # a little along, so the steps overlap
                        xy = q + side * nrm * off + t * along * RIDGE_STEP
                        ring.append((*xy, z + dz))
            section.append(ring)
        for sa, sb in zip(section[:-1], section[1:]):
            tents.append(m3.Manifold.hull_points(sa + sb))
    if not tents:
        return None, []
    # Drafted 45 deg at both ends; the floor follows the dish (a straight chord from end to end stayed
    # above HF6-4's dish in between and the band held only air, 2026-09-27).
    k = d + 12
    floor = [(float(r), face_z(p, float(r)) - k) for r in np.linspace(r0 + k, r1 - k, 40)]
    band = revolve([(r0 - 40, 80.0), (r0 - 40, face_z(p, r0) + 40), *floor,
                    (r1 + 40, face_z(p, r1) + 40), (r1 + 40, 80.0)])
    dish = offset_dish_profile(p, d)
    band = band ^ revolve([(0.0, 80.0), *dish, (p.lip_r + 5, 80.0)])
    return band, tents


def build(recipe):
    """(part manifold, report). Outline family; style features are added stage by stage."""
    import time
    m3 = _m3()
    p = recipe_from_dict(recipe) if isinstance(recipe, dict) else recipe
    if p.family != 'outline':
        raise ValueError('mesh build: outline family only')
    stages, t0 = [], time.time()
    body = revolve(blank_profile(p))

    def apply(name, tools, trim=None):
        nonlocal body
        tools = [t for t in tools if not t.is_empty()]
        if not tools:
            return
        start, before = time.time(), body.volume()
        cut = m3.Manifold.batch_boolean(tools, m3.OpType.Add)
        if trim is not None:
            cut = cut ^ trim
        body = body - cut
        stages.append({'op': name, 'tools': len(tools), 'removed_mm3': round(before - body.volume(), 1),
                       'seconds': round(time.time() - start, 3)})

    apply('through_windows', [loft(r) for r in window_rings(p, outlines(p))], trim=revolve(window_envelope_profile(p)))
    apply('spoke_grooves_outline', _round([to_manifold(t) for t in outline_groove_tools(p)], p))
    band, tents = ridge_tools(p) if p.spoke_pad_style == 'ridge' else (None, [])
    if band is not None:
        roofs = m3.Manifold.batch_boolean(_round(tents, p), m3.OpType.Add)
        apply('spoke_ridges', [band - roofs])
    pads = [] if band is not None else [loft(rings) for rings in spoke_pad_tools(p, trim=False)]
    if pads:
        dish = offset_dish_profile(p, p.spoke_pad_depth)
        above = revolve([(0.0, 80.0), *dish, (p.lip_r + 5, 80.0)])         # the pocket floor is the lowered dish
        apply('spoke_pads', _round(pads, p), trim=above)
    apply('hub_valleys', _round([to_manifold(t) for t in hub_valley_tools(p)], p))
    apply('lug_holes_and_seats', lug_tools(p))
    report = {'status': str(body.status()), 'genus': body.genus(), 'volume_mm3': round(body.volume(), 1),
              'mass_kg_6061': round(body.volume() * DENSITY_6061, 2), 'stages': stages,
              'seconds': round(time.time() - t0, 2), 'pocket_skin_mm': POCKET_SKIN}
    return body, report


def export_glb(body, path):
    """GLB for the viewer: CAD Z-up turned to glTF Y-up, as the B-Rep GLB export does."""
    import trimesh
    mesh = body.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    tm = trimesh.Trimesh(np.column_stack([v[:, 0], v[:, 2], -v[:, 1]]), np.asarray(mesh.tri_verts), process=False)
    # Normals smooth across the dish and fillets but split at edges over 30 deg (window rims, chamfers).
    trimesh.graph.smooth_shade(tm, angle=math.radians(30)).export(path, include_normals=True)


def verify(body, p, spec: dict) -> dict:
    """Checks on the mesh-built part against the specs, named as wheel_skill.verify names them."""
    m3 = _m3()
    checks = {}
    lo, hi = np.asarray(body.bounding_box()[:3]), np.asarray(body.bounding_box()[3:])
    checks["single_valid_solid"] = {"pass": body.status() == m3.Error.NoError and len(body.decompose()) == 1}
    if "diameter_in" in spec:
        checks["outer_diameter"] = {"pass": abs(hi[0] - lo[0] - 2 * p.lip_r) < .5, "measured_mm": round(float(hi[0] - lo[0]), 2),
                                    "expected_mm": round(2 * p.lip_r, 2)}
    if "width_in" in spec:
        checks["overall_width"] = {"pass": abs(hi[2] - lo[2] - p.width) < .5, "measured_mm": round(float(hi[2] - lo[2]), 2),
                                   "expected_mm": p.width}
    # Bolt holes: a pin just inside each hole meets no material; one between two holes does.
    pin = lambda a, r: _cylinder(r, -p.width - 5, 5, p.pcd / 2 * math.cos(a), p.pcd / 2 * math.sin(a), 32)
    angles = [math.radians(180 / p.spokes + i * 360 / p.bolts) for i in range(p.bolts)]
    clear = [(body ^ pin(a, p.bolt_d / 2 - .5)).volume() < 1.0 for a in angles]
    solid = (body ^ pin(angles[0] + math.pi / p.bolts, 2.0)).volume() > 1.0
    checks["bolt_pattern"] = {"pass": all(clear) and solid, "holes_found": int(sum(clear)), "expected": p.bolts, "pcd_mm": p.pcd}
    if "et_mm" in spec:
        # The mounting face just outside the bore, clear of the lug seats (at least 3 mm of it).
        r_out = max(p.center_bore_r + 4, min(p.hub_r, p.pcd / 2 - p.seat_d / 2 - 1))
        ring = _cylinder(r_out, -p.width - 5, 5) - _cylinder(p.center_bore_r + 1, -p.width - 6, 6)
        hub = body ^ ring
        et = float(hub.bounding_box()[2]) + p.width / 2 if not hub.is_empty() else float("nan")
        checks["offset_et"] = {"pass": abs(et - spec["et_mm"]) < .5, "measured_mm": round(et, 1), "expected_mm": spec["et_mm"]}
    # Lugs break the spoke symmetry unless the counts share a factor (8 groups, 5 lugs: none left).
    order = math.gcd(p.spokes, p.bolts)
    if order < 2:
        checks["rotational_symmetry"] = {"pass": True, "sector_volume_spread": None,
                                         "note": f"{p.spokes} 组辐条与 {p.bolts} 个螺栓孔没有共同的旋转对称"}
        for record in checks.values():
            record["pass"] = bool(record["pass"])
        return checks
    pitch = 2 * math.pi / order
    vols = []
    for k in range(min(order, 3)):
        a0 = k * pitch - pitch / 2
        wedge = m3.CrossSection([np.array([(0, 0), (900 * math.cos(a0), 900 * math.sin(a0)),
                                           (900 * math.cos(a0 + pitch), 900 * math.sin(a0 + pitch))])])
        vols.append((body ^ wedge.extrude(p.width + 200).translate([0, 0, -p.width - 100])).volume())
    spread = (max(vols) - min(vols)) / max(float(np.mean(vols)), 1)
    checks["rotational_symmetry"] = {"pass": spread < .01, "sector_volume_spread": round(float(spread), 4)}
    for record in checks.values():
        record["pass"] = bool(record["pass"])
    return checks
