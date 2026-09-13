"""Pure-math layout of template forged-monoblock-v4.

No CAD kernel import: the API validates specs with this module before queueing a build.
Coordinates: millimetres, wheel axis Z, rim width mid-plane Z=0, +Z is the outboard (face) side.
Rim profile points are (r, z); spoke section points are (u, z), u along the section's in-plane axis.
"""
import math

TEMPLATE_VERSION = "forged-monoblock-v4"
INCH = 25.4

# Rim contour approximating a J flange, 5° bead seat with hump, and a drop well on the outboard side.
# Not checked item by item against ETRTO / TRA tables.
FLANGE_HEIGHT = 17.5
BEAD_SEAT_WIDTH = 20.0
BEAD_SEAT_TAPER = math.radians(5)
HUMP_HEIGHT = 1.2
HUMP_LENGTH = 8.0
WELL_DEPTH = 17.0
WELL_FLANK = math.radians(25)
WELL_BOTTOM = 38.0
FLANGE_EXTRA = 5.0          # flange thickness = rim wall + this
SEAT_EXTRA = 1.5            # wall under bead seats and spoke tips = rim wall + this

SPOKE_DRAFT = math.radians(6)
TIP_DEPTH_RATIO = 0.7       # spoke depth at the rim relative to depth at the hub
HUB_PAD_PROUD = 3.0         # hub pad face stands this far in front of the spoke root apex
TIP_SETBACK = 2.0           # minimum gap between spoke tip apex and the flange outboard face
HUB_EDGE_FILLET = 4.0
POCKET_SIDE_WALL = 5.0
POCKET_FACE_SKIN = 6.0
POCKET_FLOOR_RADIUS = 3.0
LUG_POCKET_EXTRA = 14.0     # socket clearance diameter = bolt hole + this
LUG_CONE_EXTRA = 8.0        # 60° seat top diameter = bolt hole + this
LUG_SEAT_THICKNESS = 14.0   # mounting face to lug pocket floor
SPOKE_TS = (0.0, 0.25, 0.5, 0.75, 1.0)
POCKET_TS = (0.22, 0.5, 0.78)


def _unit(dr, dz):
    length = math.hypot(dr, dz)
    return dr / length, dz / length


def _offset_chain(points, thickness):
    """Mitred offset of an open polyline toward its material side, per-segment thickness."""
    lines = []
    for (a, b), t in zip(zip(points, points[1:]), thickness):
        d = _unit(b[0] - a[0], b[1] - a[1])
        n = (d[1], -d[0])  # material side for a chain running outboard → inboard over the tyre side
        lines.append(((a[0] + n[0] * t, a[1] + n[1] * t), d))
    result = [lines[0][0]]
    for (p, d1), (q, d2) in zip(lines, lines[1:]):
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) < 1e-9:
            result.append(q)
            continue
        s = ((q[0] - p[0]) * d2[1] - (q[1] - p[1]) * d2[0]) / cross
        result.append((p[0] + d1[0] * s, p[1] + d1[1] * s))
    last_point, last_d = lines[-1]
    length = math.hypot(points[-1][0] - points[-2][0], points[-1][1] - points[-2][1])
    result.append((last_point[0] + last_d[0] * length, last_point[1] + last_d[1] * length))
    return result


def round_polygon(points, radii):
    """Closed polygon with a tangent arc at each vertex whose radius > 0.

    Radii are clamped so neighbouring arcs never consume more than 98% of a side, so every side keeps
    a line segment and the edge sequence is stable (lofts need matching edge counts).
    Returns [("line", a, b) | ("arc", a, mid, b)].
    """
    count = len(points)
    corners = []
    for index, (point, radius) in enumerate(zip(points, radii)):
        prev, nxt = points[index - 1], points[(index + 1) % count]
        u1 = _unit(prev[0] - point[0], prev[1] - point[1])
        u2 = _unit(nxt[0] - point[0], nxt[1] - point[1])
        angle = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        if radius <= 0 or angle > math.pi - 1e-6:
            corners.append((point, None, point))
            continue
        half_tan = math.tan(angle / 2)
        limit = 0.49 * min(math.dist(point, prev), math.dist(point, nxt))
        distance = min(radius / half_tan, limit)
        radius = distance * half_tan
        bisector = _unit(u1[0] + u2[0], u1[1] + u2[1])
        to_center = radius / math.sin(angle / 2)
        mid = (point[0] + bisector[0] * (to_center - radius), point[1] + bisector[1] * (to_center - radius))
        entry = (point[0] + u1[0] * distance, point[1] + u1[1] * distance)
        exit_ = (point[0] + u2[0] * distance, point[1] + u2[1] * distance)
        corners.append((entry, mid, exit_))
    segments = []
    for index, (entry, mid, exit_) in enumerate(corners):
        previous_exit = corners[index - 1][2]
        segments.append(("line", previous_exit, entry))
        if mid is not None:
            segments.append(("arc", entry, mid, exit_))
    return segments


def layout(spec):
    """Derived dimensions, rim profile and spoke frames. Raises ValueError for infeasible specs."""
    D, W = spec.rim_diameter_in * INCH, spec.rim_width_in * INCH
    R, half = D / 2, W / 2
    flange = spec.rim_wall_mm + FLANGE_EXTRA
    seat_wall = spec.rim_wall_mm + SEAT_EXTRA
    wall = spec.rim_wall_mm
    junction = spec.junction_fillet_mm
    hub_r = spec.hub_diameter_mm / 2
    hub_front = spec.offset_et_mm + spec.hub_thickness_mm
    # The spoke↔rim fillet needs room below the flange face, or it wraps over the lip edge and fails.
    apex_tip = half + flange - max(TIP_SETBACK, junction + 3)
    apex_root = hub_front - HUB_PAD_PROUD
    crown = spec.spoke_crown_mm
    paired = spec.spoke_style == "paired"
    tip_width = spec.paired_gap_mm + 2 * spec.paired_tip_width_mm if paired else spec.spoke_width_rim_mm
    if paired:
        if spec.pocket_depth_mm:
            raise ValueError("双辐模板暂不支持背腔，请将背腔深度设为 0。")
        if spec.sweep_deg:
            raise ValueError("当前双辐窗口沿径向布置，请将轮辐偏转设为 0。")
        if spec.paired_tip_width_mm - spec.spoke_thickness_mm * math.tan(SPOKE_DRAFT) < 2.5:
            raise ValueError("双辐支臂过窄，拔模后的背面宽度不足，请加宽支臂或减小厚度。")
        if spec.spoke_fillet_mm > spec.paired_tip_width_mm / 3:
            raise ValueError("双辐棱边圆角过大，请减小圆角或加宽支臂。")

    if spec.offset_et_mm < -half + 10:
        raise ValueError("安装面偏距 ET 过小，中心盘超出轮辋内侧。")
    if hub_front > half + flange - 2:
        raise ValueError("ET 与中心盘厚度之和过大，中心盘超出轮缘外端面。")
    if spec.spoke_thickness_mm + crown + 6 > spec.hub_thickness_mm:
        raise ValueError("中心盘厚度不足以包住轮辐根部，请加厚中心盘或减小轮辐厚度。")
    lug_pocket = spec.bolt_diameter_mm + LUG_POCKET_EXTRA
    if spec.bolt_circle_mm + lug_pocket + 12 > spec.hub_diameter_mm:
        raise ValueError("安装孔沉孔距离中心盘外缘过近，请增加中心盘直径或减小节圆直径。")
    if spec.center_bore_mm + lug_pocket + 6 > spec.bolt_circle_mm:
        raise ValueError("中心孔与安装孔沉孔之间的间隔不足。")

    # Spokes: sections on vertical planes along a swept, dished centre path.
    r_root = hub_r - 10
    r_tip = R - 1.5 - tip_width ** 2 / (8 * R)
    if r_tip - r_root < 80:
        raise ValueError("中心盘相对轮辋过大，轮辐长度不足。")
    if spec.spoke_count * spec.spoke_width_hub_mm > 0.85 * 2 * math.pi * r_root:
        raise ValueError("轮辐根部宽度之和超过中心盘周长，相邻轮辐会重叠。")
    sweep = math.radians(spec.sweep_deg)
    k = spec.face_curve

    def frame(t):
        r = r_root + (r_tip - r_root) * t
        theta = sweep * (3 * t * t - 2 * t ** 3)
        dr, dtheta = r_tip - r_root, sweep * 6 * t * (1 - t)
        dx = dr * math.cos(theta) - r * dtheta * math.sin(theta)
        dy = dr * math.sin(theta) + r * dtheta * math.cos(theta)
        nx, ny = _unit(dx, dy)
        apex = apex_root + (apex_tip - apex_root) * ((1 - k) * t + k * t * t)
        depth = spec.spoke_thickness_mm * (1 - (1 - TIP_DEPTH_RATIO) * t)
        width = spec.spoke_width_hub_mm + (tip_width - spec.spoke_width_hub_mm) * t
        front = apex - crown
        back = front - depth
        back_half = width / 2 - depth * math.tan(SPOKE_DRAFT)
        return {"r": r, "origin": (r * math.cos(theta), r * math.sin(theta)), "xdir": (-ny, nx),
                "front": front, "back": back, "apex": apex, "depth": depth,
                "width": width, "back_half": back_half}

    # Rim: the drop well starts below the lowest spoke back near the rim, so spokes never cut into it.
    seat_r = R + BEAD_SEAT_WIDTH * math.tan(BEAD_SEAT_TAPER)
    ledge_r = seat_r + 0.5
    well_r = R - WELL_DEPTH
    clearance_r = well_r - wall - junction - 2
    samples = [frame(i / 100) for i in range(101)]
    lowest_back = min(f["back"] for f in samples if f["r"] >= clearance_r)
    ledge_start = half - BEAD_SEAT_WIDTH - HUMP_LENGTH
    shoulder = min(ledge_start, lowest_back - junction - 6)
    flank = (ledge_r - well_r) * math.tan(WELL_FLANK)
    well_start, well_end = shoulder - flank, shoulder - flank - WELL_BOTTOM
    inboard_shoulder = well_end - flank
    inboard_ledge_end = -half + BEAD_SEAT_WIDTH + HUMP_LENGTH
    if inboard_shoulder - inboard_ledge_end < 2:
        raise ValueError("轮辋宽度不足以容纳深槽和轮辐外端，请加宽轮辋、减小轮辐厚度或凹面深度。")

    tip_out, tip_in = (R + FLANGE_HEIGHT, half), (R + FLANGE_HEIGHT, -half)
    heel_out, heel_in = (R, half), (R, -half)
    seat_out, seat_in = (seat_r, half - BEAD_SEAT_WIDTH), (seat_r, -half + BEAD_SEAT_WIDTH)
    hump_out = (seat_r + HUMP_HEIGHT, half - BEAD_SEAT_WIDTH - HUMP_LENGTH / 2)
    hump_in = (seat_r + HUMP_HEIGHT, -half + BEAD_SEAT_WIDTH + HUMP_LENGTH / 2)
    shoulder_out, shoulder_in = (ledge_r, shoulder), (ledge_r, inboard_shoulder)
    well_a, well_b = (well_r, well_start), (well_r, well_end)
    ledge_in = (ledge_r, inboard_ledge_end)
    # Tyre side, outboard flange top → inboard flange top, with fillet radii.
    tyre = [(tip_out, 0.45 * flange), (heel_out, 1.5), (seat_out, 2.0), (hump_out, 3.0)]
    if ledge_start - shoulder >= 6:
        tyre.append(((ledge_r, ledge_start), 3.0))
    tyre += [(shoulder_out, 4.0), (well_a, 5.0), (well_b, 5.0), (shoulder_in, 4.0), (ledge_in, 3.0),
             (hump_in, 3.0), (seat_in, 2.0), (heel_in, 1.5), (tip_in, 0.45 * flange)]
    # The inner surface runs straight from under the outboard flange to the well shoulder, so spoke tips
    # meet a single face and the spoke↔rim fillet does not have to cross creases or tiny faces.
    inner = _offset_chain([tip_out, heel_out, shoulder_out, well_a, well_b, shoulder_in, ledge_in,
                           seat_in, heel_in, tip_in],
                          [flange, seat_wall, wall, wall, wall, wall, seat_wall, seat_wall, flange])

    def inner_radius(index):
        # Near-collinear vertices get no fillet (tiny arcs leave sliver faces); lip corners stay small.
        a, b, c = inner[index - 1], inner[index], inner[index + 1]
        turn = abs(math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(b[1] - a[1], b[0] - a[0]))
        if min(turn, 2 * math.pi - turn) < math.radians(10):
            return 0.0
        return 1.5 if index in (1, len(inner) - 2) else 4.0

    rim_polygon = (tyre + [(inner[-1], 0.45 * flange)]
                   + [(inner[index], inner_radius(index)) for index in range(len(inner) - 2, 0, -1)]
                   + [(inner[0], 0.45 * flange)])

    sections = []
    for t in SPOKE_TS:
        f = frame(t)
        points = [(-f["back_half"], f["back"]), (f["back_half"], f["back"]), (f["width"] / 2, f["front"])]
        radii = [0.6 * spec.spoke_fillet_mm, 0.6 * spec.spoke_fillet_mm, spec.spoke_fillet_mm]
        if crown > 0:
            points.append((0.0, f["apex"]))
            radii.append(f["width"])
        points.append((-f["width"] / 2, f["front"]))
        radii.append(spec.spoke_fillet_mm)
        sections.append({**f, "polygon": points, "radii": radii})

    pockets = []
    if spec.pocket_depth_mm > 0:
        for t in POCKET_TS:
            f = frame(t)
            half_width = f["back_half"] - POCKET_SIDE_WALL
            depth = min(spec.pocket_depth_mm, f["depth"] - POCKET_FACE_SKIN)
            if half_width < POCKET_FLOOR_RADIUS + 1 or depth < 3:
                raise ValueError("轮辐太窄或太薄，放不下背腔；请加宽加厚轮辐，或将背腔深度设为 0。")
            floor, outside = f["back"] + depth, f["back"] - 3
            pockets.append({**f, "polygon": [(-half_width, outside), (half_width, outside),
                                             (half_width, floor), (-half_width, floor)],
                            "radii": [0.5, 0.5, POCKET_FLOOR_RADIUS, POCKET_FLOOR_RADIUS]})

    paired_slot = None
    if paired:
        # U-shaped open slot cut through each broad group before it meets the rim.
        start = hub_r + spec.paired_split_start_mm
        radius = spec.paired_gap_mm / 2
        end = r_tip + 15
        if start + radius + 20 >= r_tip:
            raise ValueError("双辐分叉位置太靠外，支臂长度不足。")
        for t in (0.25, 0.5, 0.75, 1):
            f = frame(t)
            if f["r"] >= start + radius and f["back_half"] - radius < 2.5:
                raise ValueError("双辐窗口挤占支臂，请增大根部宽度或减小双辐间隙。")
        paired_slot = {"start_r_mm": start, "radius_mm": radius, "end_r_mm": end,
                       "gap_mm": spec.paired_gap_mm}

    return {
        "paired_slot": paired_slot,
        "rim_polygon": rim_polygon, "sections": sections, "pockets": pockets,
        "hub_front_z": hub_front, "hub_radius": hub_r,
        "well_radius": well_r, "well_mid_z": (well_start + well_end) / 2,
        "lug_pocket_diameter": lug_pocket, "lug_cone_diameter": spec.bolt_diameter_mm + LUG_CONE_EXTRA,
        "derived": {
            "spoke_group_count": spec.spoke_count,
            "spoke_blade_count": spec.spoke_count * (2 if paired else 1),
            "bead_seat_diameter_mm": round(D, 3),
            "outer_diameter_mm": round(D + 2 * FLANGE_HEIGHT, 3),
            "rim_width_mm": round(W, 3),
            "overall_width_mm": round(W + 2 * flange, 3),
            "flange_thickness_mm": round(flange, 3),
            "mounting_face_z_mm": spec.offset_et_mm,
            "backspacing_mm": round(spec.offset_et_mm + half + flange, 3),
            "concavity_mm": round(apex_tip - hub_front, 3),
            "well_diameter_mm": round(2 * well_r, 3),
            "spoke_length_mm": round(r_tip - r_root, 3),
        },
    }
