"""Semantic cross-section cages for the experimental v8 spoke surface.

The cage is still evaluated on the existing watertight topology, but unlike the
v7 distance bumps it defines an ordered edge/ridge/body/groove/edge section for
each branch and varies that section along the branch.  It is the first migration
step toward independently editable surface patches.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicSpline, PchipInterpolator


ALGORITHM = "paired-section-cage-v1"
BOUNDARY_ALGORITHM = "boundary-section-patches-v2"


def _resample(guide, count=48):
    points = np.asarray(guide, float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3:
        raise ValueError("截面控制笼的每条导线至少需要三个二维点。")
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if distance[-1] < 1e-6:
        raise ValueError("截面控制笼导线长度无效。")
    parameter = distance/distance[-1]
    sample = np.linspace(0, 1, count)
    return CubicSpline(parameter, points, axis=0, bc_type="natural")(sample), sample


def section_cage_relief(points, master, crown, ridge, groove):
    """Evaluate two asymmetric, longitudinally varying branch sections.

    Ridge and groove guides are paired by branch.  Their separation establishes
    the local section frame; no sample-specific pixel positions or wheel radius
    are used.  Areas outside both branch cages deliberately receive no inferred
    detail so the caller can blend in a conservative root-panel prior.
    """
    ridge_guides = master.get("ridge_guides", [])
    groove_guides = master.get("groove_guides", [])
    if len(ridge_guides) != len(groove_guides) or len(ridge_guides) < 2:
        raise ValueError("v8 截面控制笼需要成对的双支臂凸脊与凹槽导线。")
    p = np.asarray(points, float)
    values, weights, branches = [], [], []
    for ridge_guide, groove_guide in zip(ridge_guides, groove_guides):
        ridge_curve, station = _resample(ridge_guide)
        groove_curve, _ = _resample(groove_guide)
        center = (ridge_curve+groove_curve)/2
        # The paired image guides directly define the local cross-section axis.
        # A tangent-derived normal becomes unstable on strongly curved branches
        # when the two guides do not share identical arc-length parameterization.
        cross_axis = groove_curve-ridge_curve
        separation = np.linalg.norm(cross_axis, axis=1)
        normal = cross_axis/np.maximum(separation[:, None], 1e-9)
        half_width = np.maximum(separation/.7, 2.0)

        delta = p[:, None, :]-center[None, :, :]
        nearest = np.linalg.norm(delta, axis=2).argmin(axis=1)
        rows = np.arange(len(p))
        q = np.einsum("ij,ij->i", delta[rows, nearest], normal[nearest])/half_width[nearest]
        s = station[nearest]
        longitudinal = .72+.28*np.sin(np.pi*s)**2
        anchors = np.array([-1., -.35, 0., .35, 1.])
        heights = np.column_stack([
            np.zeros(len(p)),
            longitudinal*(crown+ridge),
            longitudinal*crown*.65,
            longitudinal*(crown-groove),
            np.zeros(len(p)),
        ])
        # PCHIP is applied per point because longitudinal heights vary by row.
        profile = np.array([PchipInterpolator(anchors, height)(np.clip(value, -1, 1))
                            for value, height in zip(q, heights)])
        proximity = np.clip(1-np.maximum(abs(q)-.82, 0)/.18, 0, 1)
        values.append(profile)
        weights.append(proximity)
        branches.append({"stations": len(station), "median_half_width_px": float(np.median(half_width)),
                         "min_half_width_px": float(half_width.min()), "max_half_width_px": float(half_width.max())})
    weights = np.asarray(weights)
    values = np.asarray(values)
    total = weights.sum(axis=0)
    target = np.divide((weights*values).sum(axis=0), total, out=np.zeros(len(p)), where=total > 1e-9)
    coverage = np.clip(total, 0, 1)
    narrow = [index for index, branch in enumerate(branches) if branch["min_half_width_px"] < 4]
    return target, coverage, {"algorithm": ALGORITHM, "branch_count": len(branches),
                              "section_order": ["edge", "ridge", "body", "groove", "edge"],
                              "branches": branches, "review_required": bool(narrow),
                              "narrow_branches": narrow,
                              "note": ("部分导线局部间距小于 4 px，需先复核截面方向；" if narrow else "")
                                      + "截面高度是受原图导线约束的造型变量，不是实测深度；根部多曲面连接仍待拆分。"}


def _cross(first, second):
    return first[..., 0]*second[..., 1]-first[..., 1]*second[..., 0]


def _boundary_sections(center, normal, boundary, fallback):
    """Return the nearest material interval on each section axis.

    A guide can sit a pixel outside a manually traced silhouette.  Picking the
    nearest intersection independently on each ray then jumps to another arm of
    a concave Y panel.  Pairing sorted polygon crossings first identifies actual
    inside intervals; the section origin is only nudged when it falls outside
    the nearest interval.
    """
    polygon = np.asarray(boundary, float)
    starts, delta = polygon, np.roll(polygon, -1, axis=0)-polygon
    adjusted, negative, positive, shifts = [], [], [], []
    for point, axis, default in zip(center, normal, fallback):
        denominator = _cross(axis, delta)
        valid = abs(denominator) > 1e-9
        offset = starts-point
        t = np.divide(_cross(offset, delta), denominator, out=np.zeros(len(delta)), where=valid)
        u = np.divide(_cross(offset, axis), denominator, out=np.zeros(len(delta)), where=valid)
        crossings = np.sort(t[valid & (u >= -1e-7) & (u <= 1+1e-7)])
        if len(crossings):
            crossings = crossings[np.r_[True, np.diff(crossings) > 1e-5]]
        intervals = [(float(a), float(b)) for a, b in zip(crossings[::2], crossings[1::2]) if b-a > 1e-4]
        if intervals:
            low, high = min(intervals, key=lambda pair: 0 if pair[0] <= 0 <= pair[1]
                            else min(abs(pair[0]), abs(pair[1])))
            margin = min(1.5, max((high-low)*.2, .25))
            origin = float(np.clip(0, low+margin, high-margin)) if high-low > 2*margin else (low+high)/2
        else:
            low, high, origin = -float(default), float(default), 0.
        adjusted.append(point+origin*axis)
        negative.append(float(np.clip(origin-low, .75, 80)))
        positive.append(float(np.clip(high-origin, .75, 80)))
        shifts.append(origin)
    return np.asarray(adjusted), np.asarray(negative), np.asarray(positive), np.asarray(shifts)


def _boundary_widths(center, normal, boundary, fallback):
    """Compatibility wrapper used by diagnostics and older experiments."""
    _, negative, positive, _ = _boundary_sections(center, normal, boundary, fallback)
    return negative, positive


def boundary_patch_relief(points, boundary, master, crown, ridge, groove):
    """Independent branch sections plus a separate root-junction patch.

    Unlike v8, guide separation only locates ridge/groove anchors.  Each side of
    each branch is measured against the evidence boundary along its own section
    axis, so a close ridge/groove pair cannot collapse the visible branch width.
    """
    ridge_guides = master.get("ridge_guides", [])
    groove_guides = master.get("groove_guides", [])
    if len(ridge_guides) != len(groove_guides) or len(ridge_guides) < 2:
        raise ValueError("v10 独立曲面补片需要成对的双支臂凸脊与凹槽导线。")
    p = np.asarray(points, float)
    values, weights, branches = [], [], []
    root_centers, branch_entries = [], []
    for ridge_guide, groove_guide in zip(ridge_guides, groove_guides):
        ridge_curve, station = _resample(ridge_guide)
        groove_curve, _ = _resample(groove_guide)
        center = (ridge_curve+groove_curve)/2
        cross_axis = groove_curve-ridge_curve
        separation = np.linalg.norm(cross_axis, axis=1)
        normal = cross_axis/np.maximum(separation[:, None], 1e-9)
        fallback = np.maximum(separation/.7, 2.0)
        guide_center = center.copy()
        center, negative, positive, center_shift = _boundary_sections(center, normal, boundary, fallback)
        span = negative+positive
        tail = span[len(span)//2:]
        tail_width = float(np.median(tail))
        candidates = np.flatnonzero((station >= .2) & (span <= tail_width*2))
        entry_index = int(candidates[0]) if len(candidates) else len(station)//2
        entry_station = float(station[entry_index])
        span_cap = float(np.clip(np.percentile(tail, 75)*1.6, 8, 36))
        scale = np.minimum(1, span_cap/np.maximum(span, 1e-9))
        negative, positive = negative*scale, positive*scale

        delta = p[:, None, :]-center[None, :, :]
        nearest = np.linalg.norm(delta, axis=2).argmin(axis=1)
        rows = np.arange(len(p))
        transverse = np.einsum("ij,ij->i", delta[rows, nearest], normal[nearest])
        q = np.where(transverse < 0, transverse/negative[nearest], transverse/positive[nearest])
        s = station[nearest]
        longitudinal = .68+.32*np.sin(np.pi*s)**2
        ridge_offset = -separation/2-center_shift
        groove_offset = separation/2-center_shift
        ridge_q = np.where(ridge_offset < 0, ridge_offset/negative, ridge_offset/positive)
        groove_q = np.where(groove_offset < 0, groove_offset/negative, groove_offset/positive)
        ridge_q, groove_q = np.clip(ridge_q, -.82, .82), np.clip(groove_q, -.82, .82)
        too_close = groove_q-ridge_q < .08
        anchor_mid = np.clip((ridge_q+groove_q)/2, -.75, .75)
        ridge_q = np.where(too_close, anchor_mid-.04, ridge_q)[nearest]
        groove_q = np.where(too_close, anchor_mid+.04, groove_q)[nearest]
        body_q = (ridge_q+groove_q)/2
        profile = []
        for value, left_anchor, middle_anchor, right_anchor, strength in zip(
                q, ridge_q, body_q, groove_q, longitudinal):
            anchors = [-1., left_anchor, middle_anchor, right_anchor, 1.]
            heights = [0., strength*(crown+ridge), strength*crown*.62,
                       strength*(crown-groove), 0.]
            profile.append(PchipInterpolator(anchors, heights)(np.clip(value, -1, 1)))
        profile = np.asarray(profile)
        proximity = np.clip(1-np.maximum(abs(q)-.9, 0)/.1, 0, 1)
        root_entry = np.clip((s-(entry_station-.06))/.14, 0, 1)
        root_entry = root_entry**3*(10-15*root_entry+6*root_entry**2)
        proximity *= root_entry
        values.append(profile)
        weights.append(proximity)
        root_centers.append(guide_center[0])
        branch_entries.append(guide_center[entry_index])
        active = slice(entry_index, -5)
        branches.append({
            "stations": len(station), "width_source": "panel-boundary-intersections",
            "median_left_width_px": float(np.median(negative[active])),
            "median_right_width_px": float(np.median(positive[active])),
            "entry_station": entry_station,
            "min_side_width_px": float(min(negative[active].min(), positive[active].min())),
            "max_side_width_px": float(max(negative[active].max(), positive[active].max())),
            "min_total_width_px": float((negative[active]+positive[active]).min()),
            "median_total_width_px": float(np.median(negative[active]+positive[active])),
            "section_span_cap_px": span_cap,
            "max_center_correction_px": float(np.max(abs(center_shift))),
            "median_guide_separation_px": float(np.median(separation)),
        })

    root_center = np.mean(root_centers, axis=0)
    root_radius = float(max(np.linalg.norm(entry-root_center) for entry in branch_entries)+8)
    root_distance = np.linalg.norm(p-root_center, axis=1)
    root_weight = np.clip(1-root_distance/root_radius, 0, 1)
    root_weight = root_weight**3*(10-15*root_weight+6*root_weight**2)
    root_value = crown*(.5+.5*root_weight)+ridge*.18*root_weight
    values.append(root_value)
    weights.append(root_weight*.85)

    weights = np.asarray(weights)
    values = np.asarray(values)
    total = weights.sum(axis=0)
    target = np.divide((weights*values).sum(axis=0), total, out=np.zeros(len(p)), where=total > 1e-9)
    coverage = np.clip(total, 0, 1)
    narrow = [index for index, branch in enumerate(branches) if branch["min_total_width_px"] < 4]
    return target, coverage, {
        "algorithm": BOUNDARY_ALGORITHM, "branch_count": len(branches),
        "section_order": ["edge", "ridge", "body", "groove", "edge"],
        "branches": branches, "review_required": bool(narrow), "narrow_branches": narrow,
        "root_patch": {"center_px": root_center.tolist(), "blend_radius_px": root_radius,
                       "independent": True, "depth_measured": False},
        "note": ("存在总宽小于 4 px 的有效截面，需复核边界；" if narrow else "")
                + "支臂左右宽度来自母扇区外缘求交；根部分叉使用独立平顺补片。高度与背面仍非实测。",
    }
