"""Fit window-method outlines and a camera pose from one labelled photo.

Input: a label from experiments/annotate (rim/hub ellipses, window and ignore polygons, group
count) and a WheelSpec whose depth assumptions (ET, hub thickness, spoke thickness, face_curve)
place the spoke front surface. Nothing here measures depth; outlines are front-planform only.

1. Camera: poses whose projected rim circle matches the labelled rim ellipse. The ellipse alone
   leaves the tilt sign and the perspective strength open; candidates are ranked by how
   consistently the groups repeat on the assumed front surface — the right camera makes every
   group look the same (leave-one-out IoU).
2. Windows: labelled material sampled on the front surface in polar (r, θ); the FIT groups are
   averaged and each open region of the averaged group becomes one outline (per-radius angular
   extent with sub-cell 0.5 crossings).
3. Score: the outlines' material vs labelled material on HELD-OUT groups, area-weighted on the front
   surface. The labels' own leave-one-out consistency bounds what any repeated outline can reach.

CLI: python -m wheelcam.window_fit LABEL.json [--photo PHOTO] [--spec SPEC.json] [--rim-in 20] --out DIR
"""
import argparse
import json
import math
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy.ndimage import gaussian_filter1d, label as components, map_coordinates
from scipy.optimize import least_squares

from .models import WheelSpec
from .photo_pose import front_z, project, rotation
from .template import FLANGE_HEIGHT, INCH, LUG_POCKET_EXTRA, layout
from .windows import HUB_KEEP_MM, _inside, check, rotate

RENDER_MAX = 1600
DR_MM = 1.0
CELLS_PER_DEG = 4
OUTLINE_POINTS = 96
CORNER_RADIUS_MM = 3.0     # outline corner rounding, > the default 2 mm window edge fillet
HUB_CLEARANCE_MM = 0.5     # outlines stay this far outside the hub clip cylinder
MAX_SECTOR_PHASE_DEG = 3.0 # tolerate annotation/perspective residuals without hiding real asymmetry


def fit_ellipse(points):
    """Algebraic conic fit x'Mx + g'x = 1 → centre, semi-axes (a ≥ b) and major-axis angle."""
    p = np.asarray(points, float)
    mean = p.mean(0)
    scale = np.abs(p - mean).max()
    x, y = ((p - mean) / scale).T
    (a, b, c, d, e), *_ = np.linalg.lstsq(np.column_stack([x * x, x * y, y * y, x, y]), np.ones(len(x)), rcond=None)
    m = np.array([[a, b / 2], [b / 2, c]])
    centre = np.linalg.solve(2 * m, [-d, -e])
    k = 1 + centre @ m @ centre
    values, vectors = np.linalg.eigh(m)
    axes = np.sqrt(k / values) * scale
    major = int(np.argmin(values))
    angle = math.degrees(math.atan2(vectors[1, major], vectors[0, major]))
    return {"cx": float(centre[0] * scale + mean[0]), "cy": float(centre[1] * scale + mean[1]),
            "a": float(axes[major]), "b": float(axes[1 - major]), "angle_deg": (angle + 90) % 180 - 90}


def ellipse_radius(e, x, y):
    t = math.radians(e["angle_deg"])
    dx, dy = np.asarray(x) - e["cx"], np.asarray(y) - e["cy"]
    return np.hypot((dx * math.cos(t) + dy * math.sin(t)) / e["a"], (-dx * math.sin(t) + dy * math.cos(t)) / e["b"])


def _ellipse_polygon(e, scale, factor=1.0, n=360):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    a = math.radians(e["angle_deg"])
    u, v = factor * e["a"] * np.cos(t), factor * e["b"] * np.sin(t)
    return list(zip((e["cx"] + u * math.cos(a) - v * math.sin(a)) * scale, (e["cy"] + u * math.sin(a) + v * math.cos(a)) * scale))


def label_masks(label, max_side=RENDER_MAX):
    """Material (rim ellipse − windows) and valid zone (hub → 0.97 rim − ignore) at a working scale."""
    w, h = label["image"]["width"], label["image"]["height"]
    s = min(1.0, max_side / max(w, h))
    size = (max(1, round(w * s)), max(1, round(h * s)))
    material, valid = Image.new("L", size, 0), Image.new("L", size, 0)
    draw_m, draw_v = ImageDraw.Draw(material), ImageDraw.Draw(valid)
    draw_m.polygon(_ellipse_polygon(label["rim"], s), fill=255)
    for window in label["windows"]:
        draw_m.polygon([(x * s, y * s) for x, y in window["points"]], fill=0)
    draw_v.polygon(_ellipse_polygon(label["rim"], s, 0.97), fill=255)
    if label.get("hub"):
        draw_v.polygon(_ellipse_polygon(label["hub"], s), fill=0)
    for region in label.get("ignore", []):
        draw_v.polygon([(x * s, y * s) for x, y in region["points"]], fill=0)
    return np.asarray(material, float) / 255, np.asarray(valid) > 127, s


class Grid:
    """Polar cells on the target window blank, even when the input is a loft spec."""

    def __init__(self, spec, groups):
        lay = layout(spec)
        self.spec, self.groups = spec, groups
        self.rs = np.arange(lay["hub_radius"] + HUB_KEEP_MM, spec.rim_diameter_in * INCH / 2 + FLANGE_HEIGHT, DR_MM)
        self.per_group = round(360 / groups * CELLS_PER_DEG)
        self.step_deg = 360 / (groups * self.per_group)
        self.thetas = (np.arange(groups * self.per_group) + 0.5) * self.step_deg
        self.area = np.repeat(self.rs[:, None], self.per_group, axis=1)

    def sample(self, pose, material, valid, scale):
        r, t = np.meshgrid(self.rs, np.radians(self.thetas), indexing="ij")
        xyz = np.stack([r * np.cos(t), r * np.sin(t), front_z(self.spec, r, method="window")], -1)
        uv = project(xyz.reshape(-1, 3), pose).reshape(r.shape + (2,)) * scale
        coords = [uv[..., 1] - 0.5, uv[..., 0] - 0.5]
        frac = map_coordinates(material, coords, order=1, mode="constant", cval=0)
        ok = map_coordinates(valid.astype(float), coords, order=0, mode="constant", cval=0) > 0.5
        return frac, ok

    def sectors(self, a):
        return a.reshape(len(self.rs), self.groups, self.per_group)


def _template(frac, ok, chosen):
    """Area-free average of the chosen groups; cells never seen count as material."""
    weight = ok[:, chosen].sum(1)
    return np.where(weight > 0, (frac[:, chosen] * ok[:, chosen]).sum(1) / np.maximum(weight, 1e-9), 1.0), weight


def _iou(material, template, ok, area):
    union = (((material | template) & ok) * area).sum()
    return float((((material & template) & ok) * area).sum() / union) if union else float("nan")


def leave_one_out(grid, frac, ok):
    f, k = grid.sectors(frac), grid.sectors(ok)
    scores = []
    for g in range(grid.groups):
        others = [j for j in range(grid.groups) if j != g]
        mean, weight = _template(f, k, others)
        scores.append(_iou(f[:, g] > 0.5, mean > 0.5, k[:, g] & (weight > 0), grid.area))
    return scores


def _aligned_iou(grid, reference, reference_ok, sector, sector_ok, max_shift_cols):
    """Best small circular shift that maps one sector onto a reference sector."""
    best = (-1.0, 0)
    for shift in range(-max_shift_cols, max_shift_cols + 1):
        moved = np.roll(sector, shift, axis=1)
        moved_ok = np.roll(sector_ok, shift, axis=1)
        overlap = reference_ok & moved_ok
        score = _iou(reference > 0.5, moved > 0.5, overlap, grid.area)
        score = -1.0 if math.isnan(score) else score
        # Deterministic tie break: prefer the smallest correction.
        candidate = (score, -abs(shift), -shift)
        incumbent = (best[0], -abs(best[1]), -best[1])
        if candidate > incumbent:
            best = (score, shift)
    return best


def sector_consensus(grid, frac, ok):
    """Align repeated sectors, reject damaged ones and return a robust master-sector template.

    The old even/odd split assumed every image had equally clean alternating groups. Real product
    photos have highlights, caliper occlusion and imperfect labels at arbitrary angles. This routine
    instead chooses a medoid sector, phase-aligns every group within a deliberately small bound, and
    uses a median template so one bad group cannot reshape every repeated spoke.
    """
    f, k = grid.sectors(frac), grid.sectors(ok)
    max_shift = max(1, round(MAX_SECTOR_PHASE_DEG / grid.step_deg))
    pair = np.eye(grid.groups)
    shifts = np.zeros((grid.groups, grid.groups), dtype=int)
    for reference in range(grid.groups):
        for group in range(grid.groups):
            if reference == group:
                continue
            score, shift = _aligned_iou(grid, f[:, reference], k[:, reference], f[:, group], k[:, group], max_shift)
            pair[reference, group], shifts[reference, group] = score, shift
    medoid = int(np.argmax(np.median(pair, axis=1)))
    offsets = shifts[medoid]
    aligned_f = np.stack([np.roll(f[:, group], int(offsets[group]), axis=1) for group in range(grid.groups)], axis=1)
    aligned_k = np.stack([np.roll(k[:, group], int(offsets[group]), axis=1) for group in range(grid.groups)], axis=1)
    quality = pair[medoid]
    coverage = aligned_k.mean(axis=(0, 2))
    median = float(np.median(quality))
    mad = float(np.median(np.abs(quality - median)))
    threshold = median - max(0.08, 2.5 * mad)
    inliers = [g for g in range(grid.groups) if quality[g] >= threshold and coverage[g] >= 0.25]
    if len(inliers) < min(3, grid.groups):
        inliers = sorted(range(grid.groups), key=lambda g: (quality[g], coverage[g]), reverse=True)[:min(3, grid.groups)]
    # Hold back the weakest quarter of otherwise accepted sectors. Outliers are validation-only too.
    hold_count = max(1, round(len(inliers) * 0.25)) if len(inliers) >= 4 else 0
    held_inliers = sorted(inliers, key=lambda g: (quality[g], coverage[g], g))[:hold_count]
    fit_groups = [g for g in inliers if g not in held_inliers]
    held_groups = [g for g in range(grid.groups) if g not in fit_groups]
    values = np.where(aligned_k[:, fit_groups], aligned_f[:, fit_groups], np.nan)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered", category=RuntimeWarning)
        template = np.nanmedian(values, axis=1)
    weight = aligned_k[:, fit_groups].sum(1)
    template = np.where(weight > 0, template, 1.0)
    return template, aligned_f, aligned_k, {
        "medoid_group": medoid,
        "fit_groups": fit_groups,
        "held_out_groups": held_groups,
        "inlier_groups": inliers,
        "outlier_groups": [g for g in range(grid.groups) if g not in inliers],
        "phase_offsets_deg": [round(int(value) * grid.step_deg, 3) for value in offsets],
        "quality_iou": [round(float(value), 4) for value in quality],
        "quality_threshold": round(threshold, 4),
        "method": "phase_aligned_medoid_median_v2",
    }


def fit_pose(label, spec, grid, material, valid, scale):
    e = label["rim"]
    lay = layout(spec)
    outer = spec.rim_diameter_in * INCH / 2 + FLANGE_HEIGHT
    rim_z = spec.rim_width_in * INCH / 2 + lay["derived"]["flange_thickness_mm"]
    t = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    ring = np.column_stack([outer * np.cos(t), outer * np.sin(t), np.full(len(t), rim_z)])
    mean_axis = (e["a"] + e["b"]) / 2
    tilt = math.acos(min(1.0, e["b"] / e["a"]))

    def make(v, distance):
        return {"cx": float(v[2]), "cy": float(v[3]), "scale_px": float(v[4]), "distance_radii": distance,
                "rotation": rotation(v[0], v[1], 0.0).tolist(), "radius_mm": outer, "reference_z_mm": rim_z,
                "angles_deg": [math.degrees(v[0]), math.degrees(v[1]), 0.0]}

    def residual(v, distance):
        uv = project(ring, make(v, distance))
        return (ellipse_radius(e, uv[:, 0], uv[:, 1]) - 1) * mean_axis

    candidates, seen = [], set()
    for distance in (3.0, 5.0, 8.0, 1e6):
        for beta in np.radians(np.arange(0, 360, 45)):
            start = [max(tilt, 0.02) * math.cos(beta), max(tilt, 0.02) * math.sin(beta), e["cx"], e["cy"], mean_axis]
            fit = least_squares(residual, start, args=(distance,),
                                bounds=([-0.9, -0.9, -np.inf, -np.inf, 0.5 * mean_axis], [0.9, 0.9, np.inf, np.inf, 1.5 * mean_axis]))
            ring_px = float(np.sqrt(np.mean(fit.fun ** 2)))
            key = (distance, round(fit.x[0], 2), round(fit.x[1], 2))
            if ring_px > 3 or key in seen:
                continue
            seen.add(key)
            pose = make(fit.x, distance)
            frac, ok = grid.sample(pose, material, valid, scale)
            loo = leave_one_out(grid, frac, ok)
            candidates.append({"pose": pose, "ring_rms_px": round(ring_px, 2), "loo_mean": float(np.nanmean(loo)),
                               "loo": [round(x, 4) for x in loo]})
    if not candidates:
        raise ValueError("没有相机候选能让外圈投影贴合标注椭圆。")
    candidates.sort(key=lambda c: c["loo_mean"], reverse=True)
    return candidates


def _crossing(row, a, b, fallback):
    if a < 0 or b >= len(row):
        return fallback
    f0, f1 = row[a], row[b]
    return a + ((f0 - 0.5) / (f0 - f1) if abs(f0 - f1) > 1e-9 else 0.5)


def _round_corners(points, radius=CORNER_RADIUS_MM, step=0.5):
    """Gaussian smoothing along arc length. Corners get roughly `radius` of rounding so the window
    edge fillet can run along the whole rim; straight and gently curved sides stay in place."""
    p = np.asarray(points, float)
    closed = np.vstack([p, p[:1]])
    length = np.r_[0, np.cumsum(np.hypot(*np.diff(closed, axis=0).T))]
    s = np.arange(0, length[-1], step)
    x = gaussian_filter1d(np.interp(s, length, closed[:, 0]), radius / step, mode="wrap")
    y = gaussian_filter1d(np.interp(s, length, closed[:, 1]), radius / step, mode="wrap")
    return list(zip(x, y))


def _clear_of_hub(points, minimum):
    """Push points radially out to `minimum` so the cutter never meets the hub clip cylinder."""
    p = np.asarray(points, float)
    r = np.hypot(p[:, 0], p[:, 1])
    p *= (np.maximum(r, minimum) / np.maximum(r, 1e-9))[:, None]
    return [tuple(q) for q in p]


def _resample(points, count=OUTLINE_POINTS):
    p = np.asarray(points, float)
    closed = np.vstack([p, p[:1]])
    length = np.r_[0, np.cumsum(np.hypot(*np.diff(closed, axis=0).T))]
    keep = np.r_[True, np.diff(length) > 1e-6]
    closed, length = closed[keep], length[keep]
    s = np.linspace(0, length[-1], count, endpoint=False)
    return [(float(x), float(y)) for x, y in zip(np.interp(s, length, closed[:, 0]), np.interp(s, length, closed[:, 1]))]


def _cell_boundary_loops(region):
    """Trace exact boundary loops of a 4-connected boolean cell region.

    Coordinates are cell-edge coordinates (row, column), not cell centres. Keeping the complete
    loop preserves hooks and concave shoulders that the former per-radius min/max envelope erased.
    """
    edges = set()
    rows, cols = region.shape
    for i, j in np.argwhere(region):
        i, j = int(i), int(j)
        if i == 0 or not region[i - 1, j]:
            edges.add(((i, j), (i, j + 1)))
        if j == cols - 1 or not region[i, j + 1]:
            edges.add(((i, j + 1), (i + 1, j + 1)))
        if i == rows - 1 or not region[i + 1, j]:
            edges.add(((i + 1, j + 1), (i + 1, j)))
        if j == 0 or not region[i, j - 1]:
            edges.add(((i + 1, j), (i, j)))
    outgoing = {}
    for start, end in edges:
        outgoing.setdefault(start, []).append(end)
    loops = []
    while edges:
        start, end = next(iter(edges))
        loop, current = [start], start
        while True:
            candidates = [candidate for candidate in outgoing.get(current, ()) if (current, candidate) in edges]
            if not candidates:
                break
            # Ordinary contours have one continuation. At a diagonal touch, prefer the turn that
            # closes the smaller local boundary instead of crossing into the neighbouring loop.
            nxt = candidates[0]
            edges.remove((current, nxt))
            current = nxt
            if current == start:
                if len(loop) >= 4:
                    loops.append(loop)
                break
            loop.append(current)
    return loops


def _loop_area(points):
    p = np.asarray(points, float)
    return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)))


def extract_windows(grid, mean, seam_deg, min_area_mm2=40.0):
    """One outline per open region of the averaged group (columns start at seam_deg).

    Swept or paired spokes can leave no radial line free of windows, so the angle is treated as
    periodic: two periods side by side, and each window is taken once, whole, from the regions that
    start in columns 1..n without touching either outer edge (a window starting exactly on the seam
    is whole only in the second copy, at column n).
    """
    n = mean.shape[1]
    doubled = np.hstack([mean, mean])
    labels, count = components(doubled < 0.5)
    area = np.hstack([grid.area, grid.area])
    step = math.radians(grid.step_deg)
    outlines = []
    for index in range(1, count + 1):
        region = labels == index
        cols = np.nonzero(region.any(0))[0]
        if cols.min() == 0 or cols.max() == 2 * n - 1 or cols.min() > n:
            continue
        if cols.max() - cols.min() >= n:
            raise ValueError("窗口在一整组内首尾相连，无法作为单个窗口。")
        if (region * area).sum() * step * DR_MM < min_area_mm2:
            continue
        loops = _cell_boundary_loops(region)
        if not loops:
            continue
        loop = max(loops, key=lambda points: abs(_loop_area(points)))
        radial0 = grid.rs[0] - DR_MM / 2
        polar = [(radial0 + row * DR_MM, math.radians(seam_deg + col * grid.step_deg)) for row, col in loop]
        boundary = [(radius * math.cos(angle), radius * math.sin(angle)) for radius, angle in polar]
        rounded = _clear_of_hub(_round_corners(boundary), grid.rs[0] + HUB_CLEARANCE_MM)
        outlines.append(_resample(rounded))
    return outlines


def rasterize(grid, outlines, seam_deg):
    """Open cells of one group for outlines in the absolute frame of group 0.

    A window may run past the group boundary, so the neighbouring groups' copies are drawn too.
    """
    r, t = np.meshgrid(grid.rs, np.radians(seam_deg + (np.arange(grid.per_group) + 0.5) * grid.step_deg), indexing="ij")
    cells = np.column_stack([(r * np.cos(t)).ravel(), (r * np.sin(t)).ravel()])
    opened = np.zeros(len(cells), bool)
    period = 360 / grid.groups
    for outline in outlines:
        for shift in (-period, 0.0, period):
            opened |= _inside(cells, rotate(outline, shift))
    return opened.reshape(r.shape)


def fit(label, spec, material, valid, scale):
    groups = label["meta"]["groups"]
    grid = Grid(spec, groups)
    candidates = fit_pose(label, spec, grid, material, valid, scale)
    pose = candidates[0]["pose"]
    frac, ok = grid.sample(pose, material, valid, scale)
    # Put the seam where no radius is open, so no window straddles the group boundary: fewest open
    # cells first, then the most solid column.
    f, k = grid.sectors(frac), grid.sectors(ok)
    everything, _ = _template(f, k, list(range(groups)))
    open_count = (everything < 0.5).sum(0)
    seam_col = int(np.lexsort((-everything.mean(0), open_count))[0])
    frac, ok = np.roll(frac, -seam_col, axis=1), np.roll(ok, -seam_col, axis=1)
    seam_deg = seam_col * grid.step_deg
    mean, f, k, consensus = sector_consensus(grid, frac, ok)
    fit_groups, held_groups = consensus["fit_groups"], consensus["held_out_groups"]
    outlines = extract_windows(grid, mean, seam_deg)
    opened = rasterize(grid, outlines, seam_deg)
    def score(chosen):
        return [round(_iou(f[:, g] > 0.5, ~opened, k[:, g], grid.area), 4) for g in chosen]
    held, fitted = score(held_groups), score(fit_groups)
    loo = leave_one_out(grid, frac, ok)
    try:
        checks, error = check(outlines, groups, layout(spec)["hub_radius"], spec.rim_diameter_in * INCH / 2 + FLANGE_HEIGHT), None
    except ValueError as exc:
        checks, error = None, str(exc)
    return {"groups": groups, "pose": pose, "camera_candidates": [{k2: v for k2, v in c.items() if k2 != "pose"} | {"angles_deg": c["pose"]["angles_deg"], "distance_radii": c["pose"]["distance_radii"]} for c in candidates[:6]],
            "fit_groups": fit_groups, "held_out_groups": held_groups,
            "held_out_iou": held, "held_out_iou_mean": round(float(np.mean(held)), 4) if held else None,
            "within_image_consistency_iou": round(float(np.mean(held)), 4) if held else None,
            "metric_scope": "within_image_consistency_not_independent_validation",
            "fit_iou": fitted, "label_loo_iou": [round(x, 4) for x in loo], "label_loo_mean": round(float(np.nanmean(loo)), 4),
            "window_outlines_mm": [[(round(x, 2), round(y, 2)) for x, y in o] for o in outlines],
            "contour_method": "full_2d_cell_boundary_v3",
            "sector_consensus": consensus,
            "spec_patch": {"spoke_method": "window", "spoke_count": groups, "spoke_phase_deg": 0.0,
                           "window_outlines_mm": [[(round(x, 2), round(y, 2)) for x, y in o] for o in outlines]},
            "window_checks": checks, "window_error": error,
            "base_surface": {"method": "window_blank_quadratic_bezier", "includes_crown": True,
                             "excluded": ["window_cuts", "face_relief", "spoke_ridges", "fillets", "hub", "rim"],
                             "outside_profile": "endpoint_clamp"},
            "note": "外圈、窗口来自单张照片标注；拟合采用 CAD 同源毛坯基面（含拱高，不含局部浅槽/凸脊/圆角）；深度、厚度沿用模板假设，相机按组间一致性选取，非标定"}


def overlay(photo, label, spec, result, path):
    with Image.open(photo) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
    s = min(1.0, 1400 / max(image.size))
    image = image.resize((round(image.width * s), round(image.height * s)))
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for window in label["windows"]:
        draw.polygon([(x * s, y * s) for x, y in window["points"]], outline=(40, 220, 90, 255))
    for g in range(result["groups"]):
        for outline in result["window_outlines_mm"]:
            pts = np.array(rotate(outline, result["spec_patch"]["spoke_phase_deg"] + g * 360 / result["groups"]))
            r = np.hypot(pts[:, 0], pts[:, 1])
            uv = project(np.column_stack([pts, front_z(spec, r, method="window")]), result["pose"]) * s
            colour = (230, 30, 140, 110) if g in result["held_out_groups"] else (230, 30, 140, 60)
            draw.polygon([tuple(p) for p in uv], fill=colour, outline=(230, 30, 140, 255))
    Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB").save(path, quality=88)


def spec_for(label, base=None, rim_in=20):
    """Validated input dimensions; fitting explicitly targets the window base surface.

    A loft spec carries dimensions while cutout outlines are not yet available;
    it does not select the surface used by Grid or the result overlay.
    """
    values = dict(base or {})
    values.update(spoke_method="loft", window_outlines_mm=[], spoke_count=label["meta"]["groups"])
    values.setdefault("rim_diameter_in", rim_in)
    if label.get("hub"):
        outer = values["rim_diameter_in"] * INCH + 2 * FLANGE_HEIGHT
        hub_ratio = float(np.mean(ellipse_radius(label["rim"], *np.array(label["hub"]["points"]).T)))
        defaults = WheelSpec().model_dump()
        # The visible disc edge can sit inside the structural hub; never go below what the lug pockets need.
        needed = (values.get("bolt_circle_mm", defaults["bolt_circle_mm"])
                  + values.get("bolt_diameter_mm", defaults["bolt_diameter_mm"]) + LUG_POCKET_EXTRA + 12)
        values["hub_diameter_mm"] = float(np.clip(round(max(hub_ratio * outer, needed + 0.5)), 140, 200))
    return WheelSpec(**{k: v for k, v in values.items() if k in WheelSpec.model_fields})


def candidate(label, project_spec, max_side=1600):
    """API result shaped like a photo analysis: outlines become suggested parameters to review.

    Pixel quantities are rescaled to at most max_side so the workbench can overlay the photo and the
    CAD projection without a full-resolution canvas.
    """
    base = project_spec.model_dump()
    spec = spec_for(label, base)
    material, valid, scale = label_masks(label)
    result = fit(label, spec, material, valid, scale)
    suggested = dict(result["spec_patch"])
    if spec.hub_diameter_mm != project_spec.hub_diameter_mm:
        suggested["hub_diameter_mm"] = spec.hub_diameter_mm
    error = result["window_error"]
    if error is None:
        try:
            WheelSpec(**{**base, **suggested})
        except ValueError as exc:
            error = str(exc).splitlines()[1].strip() if len(str(exc).splitlines()) > 1 else str(exc)
    w, h = label["image"]["width"], label["image"]["height"]
    s = min(1.0, max_side / max(w, h))
    rim = label["rim"]
    pose = {**result["pose"], "cx": result["pose"]["cx"] * s, "cy": result["pose"]["cy"] * s,
            "scale_px": result["pose"]["scale_px"] * s}
    outer = spec.rim_diameter_in * INCH + 2 * FLANGE_HEIGHT
    warnings = ["窗口轮廓来自人工标注的单张照片；只拟合正面平面形状，深度、拔模和背面沿用模板假设",
                "同图各组参与过相机和相位选择；重合度不是独立盲测或可达精度上限",
                "拟合使用 CAD 同源毛坯基面（含拱高），未比较局部浅槽、凸脊、圆角和真实完整实体",
                "相机按外圈椭圆与各组一致性选取，不是标定结果；毫米尺寸按当前项目的轮辋规格换算"]
    if "hub_diameter_mm" in suggested:
        warnings.append(f"中心盘直径按标注比例改为 {spec.hub_diameter_mm:g} mm（不小于孔系所需）")
    return {"algorithm": "window-fit-v3", "status": "candidates" if error is None else "ambiguous",
            "can_apply": error is None, "image_size": [round(w * s), round(h * s)],
            "ellipse": {"cx": rim["cx"] * s, "cy": rim["cy"] * s, "rx": rim["a"] * s, "ry": rim["b"] * s,
                        "angle_deg": rim["angle_deg"]},
            "edge_coverage": 1.0, "edge_residual_px": round(rim.get("rms_px", 0.0) * s, 2),
            "outer_points": [[x * s, y * s] for x, y in rim["points"]], "stations": [], "traces": [],
            "suggested_parameters": suggested,
            "camera_fit": {"status": "fitted", "pose": pose, "held_out_groups": result["held_out_groups"],
                           "note": "相机候选按各组一致性选取；不能由单张照片唯一恢复焦距、距离或真实深度"},
            "section_fit": {"status": "window_fit", "stations": []},
            "window_fit": {key: result[key] for key in ("held_out_iou", "held_out_iou_mean", "label_loo_mean",
                                                        "within_image_consistency_iou", "metric_scope",
                                                        "fit_groups", "held_out_groups", "window_checks", "sector_consensus", "contour_method", "base_surface")}
                          | {"window_error": error, "window_count": len(result["window_outlines_mm"])},
            "scale": {"reference_outer_mm": None, "target_outer_mm": outer, "reference_gap_mm": None,
                      "basis": "照片无实测标尺；窗口毫米尺寸按当前项目的轮辋规格换算"},
            "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("label", type=Path)
    parser.add_argument("--photo", type=Path)
    parser.add_argument("--spec", type=Path, help="WheelSpec JSON（或含 spec 字段的 JSON）提供深度假设")
    parser.add_argument("--rim-in", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    label = json.loads(args.label.read_text())
    base = None
    if args.spec:
        base = json.loads(args.spec.read_text())
        base = base.get("spec", base)
    spec = spec_for(label, base, args.rim_in)
    material, valid, scale = label_masks(label)
    result = fit(label, spec, material, valid, scale)
    result["depth_spec"] = spec.model_dump(exclude={"window_outlines_mm"})
    args.out.mkdir(parents=True, exist_ok=True)
    stem = label["image"]["sha256"][:12]
    (args.out / f"{stem}.fit.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    if args.photo:
        overlay(args.photo, label, spec, result, args.out / f"{stem}.overlay.jpg")
    summary = {k: result[k] for k in ("groups", "held_out_iou_mean", "held_out_iou", "label_loo_mean", "window_error")}
    summary["windows"] = len(result["window_outlines_mm"])
    summary["camera"] = result["camera_candidates"][0]
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
