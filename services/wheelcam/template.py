"""Pure-math layout of template forged-monoblock-v10.

No CAD kernel import: the API validates specs with this module before queueing a build.
Coordinates: millimetres, wheel axis Z, rim width mid-plane Z=0, +Z is the outboard (face) side.
Rim profile points are (r, z); spoke section points are (u, z), u along the section's in-plane axis.
v10 keeps the v9 lofted spokes (spoke_method "loft") and adds "window": a turned spoke blank with the
same dished front/back curves, minus window outlines fitted from a photo.
"""
import math

from . import windows as window_rules

TEMPLATE_VERSION = "forged-monoblock-v10"
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


def slot_half_width(slot, r):
    root = slot["radius_mm"]
    if r < slot["start_r_mm"]:
        return 0.0
    corner = slot.get("corner_radius_mm", root)
    if r < slot["start_r_mm"] + corner:
        return root-corner+math.sqrt(max(0, corner*corner-(r-slot["start_r_mm"]-corner)**2))
    q, m = slot.get("flare_start_r_mm", 0), slot.get("flare_end_r_mm", 0)
    if not q or r <= q:
        return root
    if r >= m:
        return slot["gap_mm"]/2
    t = (r-q)/(m-q)
    return root + (slot["gap_mm"]/2-root)*t*t*(3-2*t)


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
    # The window method only borrows the dished front/back curves; loft-only shape fields are ignored.
    window_method = getattr(spec, "spoke_method", "loft") == "window"
    paired = spec.spoke_style == "paired" and not window_method
    tip_width = spec.paired_gap_mm + 2 * spec.paired_tip_width_mm if paired else spec.spoke_width_rim_mm
    if not paired and not window_method and (spec.paired_blade_root_mm or spec.paired_window_root_mm or spec.paired_root_round_mm or spec.paired_gap_flare_mm or spec.paired_shoulder_mm or spec.paired_mid_mm or spec.paired_tip_inset_mm or spec.lip_extension_mm):
        raise ValueError("轮廓展开和加宽轮唇目前仅用于双辐模板。")
    if paired:
        if spec.paired_tip_inset_mm and spec.lip_extension_mm < spec.paired_tip_inset_mm + 10:
            raise ValueError("轮辐末端内收需要足够宽的轮唇承接，请增加轮唇延伸宽度。")
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
    r_tip = R - 1.5 - tip_width ** 2 / (8 * R) - (spec.paired_tip_inset_mm if paired else 0)
    if r_tip - r_root < 80:
        raise ValueError("中心盘相对轮辋过大，轮辐长度不足。")
    if not window_method and spec.spoke_count * spec.spoke_width_hub_mm > 0.85 * 2 * math.pi * r_root:
        raise ValueError("轮辐根部宽度之和超过中心盘周长，相邻轮辐会重叠。")
    if paired and spec.paired_blade_root_mm:
        if not spec.paired_window_root_mm or crown:
            raise ValueError("直顺支臂需要启用大窗口曲线，且正面拱高为 0；前后凹面保留。")
        if spec.paired_blade_root_mm < spec.paired_tip_width_mm:
            raise ValueError("支臂过渡端宽不能小于末端宽度，以保证向外逐渐收窄。")
    corner = spec.paired_root_round_mm or (spec.paired_gap_mm+spec.paired_gap_flare_mm)/2
    profile_slot = {"radius_mm":(spec.paired_gap_mm+spec.paired_gap_flare_mm)/2,
        "start_r_mm":hub_r+spec.paired_split_start_mm,"corner_radius_mm":corner,
        "gap_mm":spec.paired_gap_mm,"flare_start_r_mm":max(hub_r+spec.paired_split_start_mm+corner,r_root+(r_tip-r_root)*.25),
        "flare_end_r_mm":r_root+(r_tip-r_root)*.5}
    taper_join = hub_r+spec.paired_window_blend_mm
    def outer_half(r):
        return slot_half_width(profile_slot,r)+spec.paired_blade_root_mm+(spec.paired_tip_width_mm-spec.paired_blade_root_mm)*(r-taper_join)/(r_tip-taper_join)
    def outer_slope(r):
        q,m = profile_slot["flare_start_r_mm"],profile_slot["flare_end_r_mm"]
        slope = (spec.paired_tip_width_mm-spec.paired_blade_root_mm)/(r_tip-taper_join)
        if not spec.paired_gap_flare_mm:
            return slope
        if m <= q:
            raise ValueError("分叉渐变长度不足，请内移分叉起点。")
        t = max(0.,min(1.,(r-q)/(m-q)))
        return (profile_slot["gap_mm"]/2-profile_slot["radius_mm"])*6*t*(1-t)/(m-q)+slope
    sweep = math.radians(spec.sweep_deg)
    k = spec.face_curve

    def base_frame(t):
        r = r_root + (r_tip - r_root) * t
        theta = sweep * (3 * t * t - 2 * t ** 3)
        dr, dtheta = r_tip - r_root, sweep * 6 * t * (1 - t)
        dx = dr * math.cos(theta) - r * dtheta * math.sin(theta)
        dy = dr * math.sin(theta) + r * dtheta * math.cos(theta)
        nx, ny = _unit(dx, dy)
        apex = apex_root + (apex_tip - apex_root) * ((1 - k) * t + k * t * t)
        depth = spec.spoke_thickness_mm * (1 - (1 - TIP_DEPTH_RATIO) * t)
        width = spec.spoke_width_hub_mm + (tip_width - spec.spoke_width_hub_mm) * t
        if paired:
            # Extra width at the quarter and middle loft sections controls the visible shoulders.
            knots = [(0, 0), (0.25, spec.paired_shoulder_mm), (0.5, spec.paired_mid_mm), (0.75, 0), (1, 0)]
            for (ta, wa), (tb, wb) in zip(knots, knots[1:]):
                if ta <= t <= tb:
                    u = (t - ta) / (tb - ta)
                    width += wa + (wb - wa) * u * u * (3 - 2 * u)
                    break
        if paired and spec.paired_blade_root_mm:
            width = 2*outer_half(r)
        front = apex - crown
        back = front - depth
        back_half = width / 2 - (0 if paired and spec.paired_blade_root_mm else depth * math.tan(SPOKE_DRAFT))
        return {"r": r, "origin": (r * math.cos(theta), r * math.sin(theta)), "xdir": (-ny, nx),
                "front": front, "back": back, "apex": apex, "depth": depth,
                "width": width, "back_half": back_half}

    # A shared window cap expands the two neighbouring outer edges into a web.
    # Express its two cubic halves in the gap bisector frame, then map each half
    # back to the spoke's radial sections. The loft itself carries the curve.
    window = None
    section_ts = list(SPOKE_TS)
    if paired and spec.paired_window_root_mm:
        alpha = math.pi / spec.spoke_count
        ca, sa = math.cos(alpha), math.sin(alpha)
        bottom = hub_r + spec.paired_window_root_mm
        join_r = hub_r + spec.paired_window_blend_mm
        if join_r <= bottom + 15 or join_r >= r_tip - 35:
            raise ValueError("大窗口过渡终点须比底部至少外移 15 mm，且不能接近轮辋。")
        if spec.paired_blade_root_mm and join_r < profile_slot["start_r_mm"]+corner+2:
            raise ValueError("支臂直顺过渡必须位于小 U 槽底角之外。")
        join_t = (join_r-r_root)/(r_tip-r_root)
        end = base_frame(join_t)
        derivative = (base_frame(join_t+.0001)["width"]-base_frame(join_t-.0001)["width"])/(2*.0001*(r_tip-r_root))/2
        x, y = join_r*ca+end["width"]/2*sa, join_r*sa-end["width"]/2*ca
        dx, dy = ca+derivative*sa, sa-derivative*ca
        if y < 4 or dy <= 0 or x <= bottom+10:
            raise ValueError("大窗口转接处太窄或外边界尚未展开，请增大过渡长度或减小辐根展开。")
        handle = min((x-bottom)*.45/dx, y*.75/dy)
        controls = [(bottom,0), (bottom,y*.55), (x-handle*dx,y-handle*dy), (x,y)]
        def cap(t):
            weights = ((1-t)**3, 3*(1-t)**2*t, 3*(1-t)*t*t, t**3)
            X,Y = (sum(w*p[k] for w,p in zip(weights,controls)) for k in (0,1))
            return X*ca+Y*sa, X*sa-Y*ca
        cap_points = [cap(i/100) for i in range(101)]
        if any(b[0] <= a[0] for a,b in zip(cap_points,cap_points[1:])):
            raise ValueError("大窗口曲线发生回折，请缩小底部外移或增大过渡长度。")
        window = {"bottom_radius_mm":bottom, "join_radius_mm":join_r,
                  "half_cap_bezier_xy":controls, "rotation_offset_deg":180/spec.spoke_count,
                  "scope":"正面大窗口对称曲线；空间厚度沿用模板假设"}
        # Include the closure and both sides of it; densely sample the curved edge
        # instead of asking a five-section loft to invent the root transition.
        section_ts = sorted(set([*SPOKE_TS, *[(cap(i/4)[0]-r_root)/(r_tip-r_root) for i in range(5)],
                                 (cap(0)[0]-r_root-3)/(r_tip-r_root)]))

    def frame(t):
        f = base_frame(t)
        if window and f["r"] <= join_r:
            r = f["r"]
            start_r,start_y = cap_points[0]
            if r < start_r:
                half_width = min(start_y+(start_r-r)*ca/sa, r*sa/ca+6)
            else:
                lo,hi = 0.,1.
                for _ in range(40):
                    mid = (lo+hi)/2
                    if cap(mid)[0] < r: lo=mid
                    else: hi=mid
                half_width = cap((lo+hi)/2)[1]
            f["width"] = 2*half_width
            f["back_half"] = half_width-(0 if spec.paired_blade_root_mm else f["depth"]*math.tan(SPOKE_DRAFT))
        return f

    explicit_profile = None
    if paired and spec.paired_blade_root_mm:
        # Exact plan-view cubic edges: no loft is allowed to overshoot these boundaries.
        root = (r_root,frame(0)["width"]/2)
        transformed = [(X*ca+Y*sa,X*sa-Y*ca) for X,Y in controls]
        segments = [[root,transformed[0]],transformed]
        breaks = sorted(set([taper_join,r_tip,*[r for r in (profile_slot["flare_start_r_mm"],profile_slot["flare_end_r_mm"]) if taper_join<r<r_tip]]))
        for a,b in zip(breaks,breaks[1:]):
            d = (b-a)/3
            segments.append([(a,outer_half(a)),(a+d,outer_half(a)+d*outer_slope(a)),
                             (b-d,outer_half(b)-d*outer_slope(b)),(b,outer_half(b))])
        explicit_profile = {"upper_segments_xy":segments,"wall_type":"vertical",
                            "taper_start_r_mm":taper_join,"taper_end_r_mm":r_tip,
                            "blade_root_width_mm":spec.paired_blade_root_mm,"blade_tip_width_mm":spec.paired_tip_width_mm}

    # Rim: the drop well starts below the lowest spoke back near the rim, so spokes never cut into it.
    seat_r = R + BEAD_SEAT_WIDTH * math.tan(BEAD_SEAT_TAPER)
    ledge_r = seat_r + 0.5
    well_r = R - WELL_DEPTH
    clearance_r = well_r - wall - junction - 2
    samples = [frame(i / 100) for i in range(101)]
    lowest_back = min(f["back"] for f in samples if f["r"] >= min(clearance_r, r_tip - 1))
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
    for t in section_ts:
        f = frame(t)
        points = [(-f["back_half"], f["back"]), (f["back_half"], f["back"]), (f["width"] / 2, f["front"])]
        radii = [0.6 * spec.spoke_fillet_mm, 0.6 * spec.spoke_fillet_mm, spec.spoke_fillet_mm]
        if crown > 0:
            points.append((0.0, f["apex"]))
            radii.append(f["width"])
        points.append((-f["width"] / 2, f["front"]))
        radii.append(spec.spoke_fillet_mm)
        if explicit_profile:
            radii = [0.0]*len(points)
        sections.append({**f, "polygon": points, "radii": radii, **({"window_join": True} if window and abs(f["r"]-join_r)<1e-7 else {})})

    pockets = []
    if spec.pocket_depth_mm > 0 and not window_method:
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
        radius = (spec.paired_gap_mm + spec.paired_gap_flare_mm) / 2
        corner = spec.paired_root_round_mm or radius
        if corner > radius or (0 < corner < .5):
            raise ValueError("分叉底部圆角须不小于 0.5 mm 且不大于根部半间隙；0 表示原半圆。")
        end = r_tip + 15
        if start + corner + 20 >= r_tip:
            raise ValueError("双辐分叉位置太靠外，支臂长度不足。")
        paired_slot = {"start_r_mm": start, "radius_mm": radius, "end_r_mm": end,
                       "gap_mm": spec.paired_gap_mm, "root_gap_mm": 2*radius, "corner_radius_mm": corner,
                       "flare_start_r_mm": max(start+corner, frame(.25)["r"]),
                       "flare_end_r_mm": frame(.5)["r"]}
        if spec.paired_gap_flare_mm and paired_slot["flare_end_r_mm"]-paired_slot["flare_start_r_mm"] < 10:
            raise ValueError("分叉渐变长度不足，请减小分叉展开或内移分叉起点。")
        check_ts = [i/100 for i in range(101)]
        if window:
            check_ts += [(p[0]-r_root)/(r_tip-r_root) for p in cap_points]
        for t in check_ts:
            f = frame(t)
            if (not window or f["r"] >= join_r) and f["width"] > 1.8 * f["r"] * math.sin(math.pi / spec.spoke_count):
                raise ValueError("双辐展开过宽，相邻组之间的窗口不足，请减小展开量。")
            if f["r"] >= (start if spec.paired_root_round_mm else start + corner) and f["back_half"] - slot_half_width(paired_slot, f["r"]) < 2.5:
                raise ValueError("双辐窗口挤占支臂，请增大根部宽度或减小双辐间隙。")

    lip = None
    if spec.lip_extension_mm:
        top = half + flange - 2
        outer, inner = R + FLANGE_HEIGHT - 2, R - spec.lip_extension_mm
        lip = {"polygon": [(outer, top), (inner, top - spec.lip_drop_mm),
                           (inner, top - spec.lip_drop_mm - 6), (outer, top - 6)],
               "radii": [1.2, 1.2, 1.2, 1.2], "inner_radius_mm": inner,
               "radial_width_mm": outer - inner, "drop_mm": spec.lip_drop_mm}
    window_blank = None
    if window_method:
        # Turned blank: the v9 quadratic front/back between the first and last section, revolved.
        f0, f1 = sections[0], sections[-1]
        r0, r1, delta = f0["r"], f1["r"], f1["front"] - f0["front"]
        top = [(r0, f0["front"]), ((r0 + r1) / 2, f0["front"] + delta * (1 - spec.face_curve) / 2), (r1, f1["front"])]
        bottom = [(r, z - d) for (r, z), d in zip(top, [f0["depth"], (f0["depth"] + f1["depth"]) / 2, f1["depth"]])]
        rim_outer = R + FLANGE_HEIGHT
        checks = window_rules.check(spec.window_outlines_mm, spec.spoke_count, hub_r, rim_outer)
        window_blank = {"top_rz": top, "bottom_rz": bottom, "outlines_mm": spec.window_outlines_mm,
                        "hub_keep_radius_mm": hub_r + window_rules.HUB_KEEP_MM,
                        "edge_fillet_mm": spec.window_edge_fillet_mm, **checks,
                        "scope": "正面窗口轮廓来自照片拟合；侧壁竖直、无拔模，前后曲面与厚度沿用模板假设"}
    skeleton_stations = []
    if paired:
        for t in (.125,.25,.5,.75,1):
            f = frame(t)
            gap = slot_half_width(paired_slot,f["r"])
            skeleton_stations.append({"fraction":t, "radius_mm":round(f["r"],2),
                "group_width_mm":round(f["width"],2), "gap_mm":round(2*gap,2),
                "blade_width_mm":round(f["width"]/2-gap,2),
                "back_blade_width_mm":round(f["back_half"]-gap,2), "depth_mm":round(f["depth"],2)})
    return {
        "window_blank": window_blank,
        "explicit_profile": explicit_profile,
        "skeleton_stations": skeleton_stations,
        "interspoke_window": window,
        "front_lip": lip,
        "paired_slot": paired_slot,
        "rim_polygon": rim_polygon, "sections": sections, "pockets": pockets,
        "hub_front_z": hub_front, "hub_radius": hub_r,
        "well_radius": well_r, "well_mid_z": (well_start + well_end) / 2,
        "lug_pocket_diameter": lug_pocket, "lug_cone_diameter": spec.bolt_diameter_mm + LUG_CONE_EXTRA,
        "derived": {
            "spoke_group_count": spec.spoke_count,
            "spoke_blade_count": spec.spoke_count * (2 if paired else 1),
            **({"window_count": len(spec.window_outlines_mm) * spec.spoke_count} if window_method else {}),
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
