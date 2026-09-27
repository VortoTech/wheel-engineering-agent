"""Photo -> forged-blank recipe: fit spoke parameters to windows traced on one spoke group.

Inputs are image-pixel clicks: >= 5 points on the outer lip edge, the hub centre, the windows of ONE
spoke group (polygons), and the group count. The base recipe supplies the envelope (lip radius, depth
profile), which comes from specs, not from the photo.

Camera: weak perspective. Every circle on the face projects to the same-shaped ellipse; its centre
moves along one image direction in proportion to depth. The rim ellipse gives the shape, the hub
click gives the shift per mm of depth (the hub face sits at `hub_z`), and the recipe's `z_top(r)` puts
each traced point at its depth. Ellipse orientation leaves the spoke phase free (fitted relative to
the spoke axis) and the handedness open (so `spoke_sweep_deg` is not fitted).

Nothing here measures depth, thickness or back-side features: those stay recipe assumptions.
"""
import math
from dataclasses import asdict, replace

import numpy as np

from scipy.optimize import minimize

from .forged_blank import ForgedWheel, recipe_from_dict, spoke_geometry, z_top
from .window_fit import fit_ellipse


class FaceMap:
    """Image pixels <-> face polar coordinates (r mm, theta rad) on the recipe's front surface."""

    def __init__(self, p: ForgedWheel, rim_points, hub_point):
        if len(rim_points) < 5:
            raise ValueError("外圈至少需要 5 个点才能拟合椭圆。")
        self.p = p
        self.e = fit_ellipse(rim_points)
        if not (self.e["a"] > 0 and self.e["b"] > 0 and self.e["b"] / self.e["a"] > .2):
            raise ValueError("外圈椭圆拟合失败或过扁，请在外圈上更均匀地取点。")
        phi = math.radians(self.e["angle_deg"])
        self.rot = np.array([[math.cos(phi), -math.sin(phi)], [math.sin(phi), math.cos(phi)]])
        self.centre = np.array([self.e["cx"], self.e["cy"]])
        # Rim ellipse is the lip edge at z = 0; the hub click is the axis at the hub face (hub_z < 0).
        self.shift = (np.asarray(hub_point, float) - self.centre) / p.hub_z

    def to_image(self, r, theta):
        r, theta = np.asarray(r, float), np.asarray(theta, float)
        local = np.stack([self.e["a"] * r / self.p.lip_r * np.cos(theta),
                          self.e["b"] * r / self.p.lip_r * np.sin(theta)], axis=-1)
        z = np.vectorize(lambda value: z_top(self.p, value))(r)
        return self.centre + local @ self.rot.T + z[..., None] * self.shift

    def to_face(self, points):
        pts = np.asarray(points, float)
        z = np.zeros(len(pts))
        for _ in range(8):                      # fixed point: depth depends on radius
            local = (pts - self.centre - z[:, None] * self.shift) @ self.rot
            x, y = local[:, 0] / self.e["a"] * self.p.lip_r, local[:, 1] / self.e["b"] * self.p.lip_r
            r = np.hypot(x, y)
            z = np.array([z_top(self.p, value) for value in r])
        return r, np.arctan2(y, x)


def _inside(px, py, polygon):
    """Vectorised even-odd point-in-polygon."""
    poly = np.asarray(polygon, float)
    x0, y0 = poly[:, 0], poly[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    px, py = np.asarray(px, float)[..., None], np.asarray(py, float)[..., None]
    crosses = ((y0 > py) != (y1 > py)) & (px < (x1 - x0) * (py - y0) / np.where(y1 == y0, 1e-12, y1 - y0) + x0)
    return np.count_nonzero(crosses, axis=-1) % 2 == 1


def _angular_width(polygon_xy, r, step_deg=.1):
    """Total angle (rad) of the circle of radius r inside the polygon (face mm coordinates)."""
    t = np.radians(np.arange(-180, 180, step_deg))
    return np.count_nonzero(_inside(r * np.cos(t), r * np.sin(t), polygon_xy)) * math.radians(step_deg)


def _angular_interval(polygon_xy, r, step_deg=.1):
    """(start, end) angle in rad of the polygon on circle r, relative to its own mean direction."""
    t = np.radians(np.arange(-180, 180, step_deg))
    hit = _inside(r * np.cos(t), r * np.sin(t), polygon_xy)
    if not hit.any():
        return None
    mean = math.atan2(np.sin(t[hit]).sum(), np.cos(t[hit]).sum())
    rel = (t[hit] - mean + math.pi) % (2 * math.pi) - math.pi
    return mean + rel.min(), mean + rel.max()


def _rotate(points, angle):
    c, s = math.cos(angle), math.sin(angle)
    return [(x * c - y * s, x * s + y * c) for x, y in points]


def fit_recipe(base: dict, rim_points, hub_point, windows_px, groups: int, bolts: int | None = None):
    """Recipe fitted to one group's traced windows. Returns (recipe dict, report dict)."""
    p = recipe_from_dict({**base, "spokes": groups, **({"bolts": bolts} if bolts else {})})
    if not windows_px:
        raise ValueError("请至少描出一个窗口。")
    face = FaceMap(p, rim_points, hub_point)
    windows = []
    for polygon in windows_px:
        if len(polygon) < 3:
            raise ValueError("每个窗口至少需要 3 个点。")
        r, theta = face.to_face(polygon)
        windows.append({"r": r, "theta": theta, "xy": np.column_stack([r * np.cos(theta), r * np.sin(theta)])})
    pitch = 2 * math.pi / groups
    warnings = []
    r_in = float(min(w["r"].min() for w in windows))
    r_out = float(max(w["r"].max() for w in windows))

    if len(windows) == 1:
        family, inter = "single", windows[0]
        start, end = _angular_interval(inter["xy"], (r_in + r_out) / 2)
        axis = (start + end) / 2 + pitch / 2        # spoke axis: half a pitch from the window centre
    elif len(windows) == 2:
        family = "y_split"
        fork, inter = sorted(windows, key=lambda w: w["r"].min(), reverse=True)   # fork starts further out
        start, end = _angular_interval(fork["xy"], (fork["r"].min() + r_out) / 2)
        axis = (start + end) / 2                    # the fork window is centred on the spoke axis
    else:
        raise ValueError("一组内超过 2 个窗口：自动拟合只支持单辐与 Y 形分叉，网状结构请导入节点图配方。")

    def material(r):                                 # spoke material width (mm) at radius r in one pitch
        return r * (pitch - sum(_angular_width(w["xy"], r) for w in windows))

    fit = {"family": family, "spokes": groups, "window_r_in": round(r_in, 1), "window_r_out": round(r_out, 1)}
    hub_r = min(r_in + 8, r_out - 1)
    fit["stem_w_hub"] = round(material(hub_r), 1)
    if family == "single":
        fit["stem_w_split"] = round(material(r_out - 8), 1)
    else:
        split = float(fork["r"].min())
        fit["split_r"] = round(split, 1)
        fit["stem_w_split"] = round(material(max(split - 4, hub_r)), 1)
        # At the outer radius each arm is the material between the fork and the inter window.
        ra = r_out - 10
        fork_iv = _angular_interval(fork["xy"], ra)
        inter_iv = _angular_interval(inter["xy"], ra)
        if not fork_iv or not inter_iv:
            raise ValueError("分叉窗口或组间窗口没有伸到外圈附近，请检查描出的窗口。")
        half_fork = (fork_iv[1] - fork_iv[0]) / 2
        half_inter = (inter_iv[1] - inter_iv[0]) / 2
        gap = pitch / 2 - half_inter - half_fork     # arm angular width
        fit["arm_w"] = round(ra * gap, 1)
        fit["arm_angle_deg"] = round(math.degrees(half_fork + gap / 2), 2)
        fit["arm_bow"] = 0.0
    recipe = asdict(recipe_from_dict({**asdict(p), **fit}))

    # Refine: the measured values carry biases (fillets, the flared stem), so adjust the spoke
    # parameters to maximise window IoU against the traced group, rasterised in one pitch sector.
    traced = [_rotate(w["xy"].tolist(), -axis) for w in windows]
    grid = _sector_grid(pitch, recipe["window_r_in"] - 12, recipe["window_r_out"] + 12)
    target = _traced_mask(traced, grid, pitch)
    start, warnings = _constrain(recipe, p)      # what the measured fit had to give up to be buildable
    initial = _window_iou(start, grid, target)
    refined, extra = _constrain(_refine(start, grid, target, p), p)
    iou = _window_iou(refined, grid, target)
    recipe, iou = (refined, iou) if iou >= initial else (start, initial)
    warnings += [note for note in extra if note not in warnings] if recipe is refined else []
    # Overlay for eyeballing: the fitted spoke footprints projected back onto the photo.
    fitted = recipe_from_dict(recipe)
    overlay = []
    for k in range(groups):
        for polygon in spoke_geometry(fitted)[0]:
            pts = np.array(_rotate(polygon, axis + k * pitch))
            r = np.minimum(np.hypot(pts[:, 0], pts[:, 1]), fitted.ring_r)
            overlay.append(np.round(face.to_image(r, np.arctan2(pts[:, 1], pts[:, 0])), 1).tolist())
    report = {"overlay_spokes_px": overlay, "family": family, "groups": groups, "window_iou": round(iou, 3), "window_iou_before_refine": round(initial, 3), "warnings": warnings,
              "rim_ellipse": face.e, "depth_shift_px_per_mm": [round(v, 4) for v in face.shift],
              "fitted": {k: recipe[k] for k in fit if k in recipe}, "method": "forged-photo-fit-v1 (weak perspective)",
              "limits": "只拟合正面可见的窗口平面轮廓；深度、厚度、背面、偏转方向均来自配方假设。"}
    return recipe, report


def _sector_grid(pitch, r_lo, r_hi, step=1.0):
    """Sample points (x, y) covering one pitch sector centred on the spoke axis (+X)."""
    xs = np.arange(-r_hi, r_hi + step, step)
    gx, gy = np.meshgrid(xs, xs)
    r, t = np.hypot(gx, gy), np.arctan2(gy, gx)
    keep = (r >= r_lo) & (r <= r_hi) & (np.abs(t) <= pitch / 2)
    return gx[keep], gy[keep]


def _traced_mask(traced, grid, pitch):
    """Window mask of the traced group, repeated to the neighbouring positions."""
    px, py = grid
    mask = np.zeros(len(px), bool)
    for k in (-1, 0, 1):
        c, s = math.cos(-k * pitch), math.sin(-k * pitch)
        qx, qy = px * c - py * s, px * s + py * c
        for polygon in traced:
            mask |= _inside(qx, qy, polygon)
    return mask


def _model_mask(recipe, grid):
    """Window mask of a recipe: the window band minus spoke footprints (no OCC, fillets ignored)."""
    p = recipe_from_dict(recipe)
    px, py = grid
    polys, _ = spoke_geometry(p)
    pitch = 2 * math.pi / p.spokes
    material = np.zeros(len(px), bool)
    for k in (-1, 0, 1):
        c, s = math.cos(-k * pitch), math.sin(-k * pitch)
        qx, qy = px * c - py * s, px * s + py * c
        for polygon in polys:
            material |= _inside(qx, qy, polygon)
    r = np.hypot(px, py)
    return (r >= p.window_r_in) & (r <= p.window_r_out) & ~material


def _window_iou(recipe, grid, target):
    model = _model_mask(recipe, grid)
    union = np.count_nonzero(model | target)
    return np.count_nonzero(model & target) / union if union else 0.0


REFINE_KEYS = {"single": ("window_r_in", "window_r_out", "stem_w_hub", "stem_w_split"),
               "y_split": ("window_r_in", "window_r_out", "stem_w_hub", "stem_w_split", "split_r",
                           "arm_angle_deg", "arm_w", "arm_bow")}


def _constrain(recipe, p):
    """Keep a fitted recipe buildable: lug-seat and ring clearance, a modellable stem, minimum widths."""
    recipe, notes = dict(recipe), []
    seats = p.pcd / 2 + p.seat_d / 2 + .5
    if recipe["window_r_in"] < seats:
        notes.append(f"窗口内缘 {recipe['window_r_in']:.1f} mm 与螺栓沉孔冲突，已调整为 {seats:.1f} mm。")
        recipe["window_r_in"] = round(seats, 1)
    if recipe["window_r_out"] > p.ring_r - 2:
        notes.append(f"窗口外缘 {recipe['window_r_out']:.1f} mm 超出模板外圈，已调整为 {p.ring_r - 2:.1f} mm。")
        recipe["window_r_out"] = round(p.ring_r - 2, 1)
    if recipe["family"] == "y_split" and recipe["split_r"] < recipe["window_r_in"] + 17:
        recipe["split_r"] = round(recipe["window_r_in"] + 17, 1)
        notes.append("分叉位置离窗口内缘太近，已外移以保持主干可建模。")
    for key in ("stem_w_hub", "stem_w_split", "arm_w"):
        if key in recipe and recipe["family"] != "single" or key != "arm_w":
            if key in recipe and recipe[key] < 8:
                notes.append(f"{key} 仅 {recipe[key]} mm，已限制为 8 mm；请检查窗口描点。")
                recipe[key] = 8.0
    return recipe, notes


def _refine(recipe, grid, target, p, max_evaluations=250):
    keys = REFINE_KEYS[recipe["family"]]
    x0 = np.array([recipe[k] for k in keys], float)
    step = np.array([1.0 if k == "arm_angle_deg" else 4.0 for k in keys])

    def cost(x):
        candidate = {**recipe, **dict(zip(keys, (x * step).tolist()))}
        if _constrain(candidate, p)[1]:          # outside the buildable range
            return 1.0
        if candidate["window_r_in"] >= candidate["window_r_out"] - 20 or min(
                candidate[k] for k in keys if k not in ("arm_bow", "window_r_in", "window_r_out")) < 6:
            return 1.0
        if candidate["family"] == "y_split" and candidate["split_r"] < candidate["window_r_in"] + 17:
            return 1.0
        try:
            return 1.0 - _window_iou(candidate, grid, target)
        except (ValueError, ZeroDivisionError):
            return 1.0
    result = minimize(cost, x0 / step, method="Nelder-Mead",
                      options={"maxfev": max_evaluations, "xatol": .05, "fatol": 1e-4})
    best = {**recipe, **{k: round(v, 2) for k, v in zip(keys, (result.x * step).tolist())}}
    return best if cost(result.x) <= cost(x0 / step) else recipe


# ---------------------------------------------------------------------------------------------
# Automatic proposals from the image, given the rim and hub clicks.
#
# The face is unwarped into polar coordinates with the FaceMap and cut into N sectors. Spoke
# material looks the same in every sector; the view through the windows does not. So:
#  * group count: the N whose sectors agree best. Divisors of the true count agree too, so step up
#    from the best N to a multiple only while it stays close (multiples misalign badly);
#  * windows: the median sector split into material / window (2-means on colour and sector spread;
#    material is the class along the hub band and the outer ring), cleaned with wrap-around
#    morphology (the angle is periodic), traced into polygons and mapped back to image pixels.
# Tested on real product photos (WORK, HF6-4) and synthetic images; not a general detector.
# ---------------------------------------------------------------------------------------------

AUTO_DR_MM, AUTO_DTH_DEG = 1.0, .5


def _sector_stack(image, p, face, groups, arc=None):
    """Every sector's samples, or with `arc` = (start, length) rad only the whole sectors inside it."""
    from scipy.ndimage import map_coordinates
    pitch = 2 * math.pi / groups
    rs = np.arange(p.hub_r + 4, p.ring_r - 1, AUTO_DR_MM)
    ths = np.radians(np.arange(0, math.degrees(pitch), AUTO_DTH_DEG))
    rr, tt = np.meshgrid(rs, ths, indexing="ij")
    start, count = (0.0, groups) if arc is None else (arc[0], int(arc[1] // pitch + 1e-9))
    stack = []
    for k in range(count):
        xy = face.to_image(rr.ravel(), (tt + start + k * pitch).ravel())
        stack.append(np.stack([map_coordinates(image[..., c], [xy[:, 1], xy[:, 0]], order=1, mode="nearest").reshape(rr.shape)
                               for c in range(3)], -1))
    return np.array(stack), rs, ths


def auto_group_count(image, base: dict, rim_points, hub_point, candidates=range(3, 13), arc=None):
    """Rotational repeat count of the spoke pattern. Returns (count, {n: mean sector spread}).

    `arc` (start, length rad): compare only the sectors inside it, for an oblique photo whose far side
    shows the barrel through the windows (HF6-5's 3/4 shot read as 3 groups over the whole turn);
    counts with fewer than two sectors in the arc are not tried."""
    spread = {}
    for n in candidates:
        if arc is not None and arc[1] < 2 * 2 * math.pi / n:
            continue
        p = recipe_from_dict({**base, "spokes": n})
        stack, _, _ = _sector_stack(image, p, FaceMap(p, rim_points, hub_point), n, arc)
        spread[n] = float(np.std(stack.mean(-1), 0).mean())
    return choose_group_count(spread), {n: round(v, 4) for n, v in spread.items()}


def choose_group_count(spread: dict) -> int:
    """Best-agreeing N, stepped up to a multiple while that multiple still clearly agrees.

    Divisors of the true count agree too, so the minimum is often a divisor. Agreement is scored
    against the median spread over all N (what misaligned sectors look like): 1 = as good as the best,
    0 = no better than misaligned. A multiple is taken while its score stays >= 0.5. A plain ratio to
    the best failed on a glossy wheel where every sector is noisy (27216: 12 chosen instead of 6).
    """
    best = min(spread, key=spread.get)
    baseline = float(np.median(list(spread.values())))
    span = max(baseline - spread[best], 1e-9)
    score = {n: (baseline - v) / span for n, v in spread.items()}
    chosen, stepped = best, True
    while stepped:
        stepped = False
        for m in range(2 * chosen, max(spread) + 1, chosen):
            if m in score and score[m] >= .5:
                chosen, stepped = m, True
                break
    return chosen


def _two_means(x, iters=20):
    centres = np.array([np.percentile(x, 10, 0), np.percentile(x, 90, 0)])
    for _ in range(iters):
        labels = np.argmin(((x[:, None, :] - centres[None]) ** 2).sum(-1), 1)
        centres = np.array([x[labels == k].mean(0) if np.any(labels == k) else centres[k] for k in (0, 1)])
    return labels


def auto_windows(image, base: dict, rim_points, hub_point, groups: int):
    """One group's window polygons in image pixels, plus notes on anything set aside."""
    from scipy.ndimage import binary_closing, binary_opening, label as components
    from .window_fit import _cell_boundary_loops, _loop_area

    p = recipe_from_dict({**base, "spokes": groups})
    face = FaceMap(p, rim_points, hub_point)
    stack, rs, ths = _sector_stack(image, p, face, groups)
    median, spread = np.median(stack, 0), np.std(stack.mean(-1), 0)
    labels = _two_means(np.concatenate([median, spread[..., None] * 2], -1).reshape(-1, 4)).reshape(median.shape[:2])
    border = np.concatenate([labels[:3].ravel(), labels[-3:].ravel()])        # hub band + outer ring
    material = np.bincount(border, minlength=2).argmax()
    pad = 8
    raw = labels != material
    wrapped = np.hstack([raw[:, -pad:], raw, raw[:, :pad]])
    mask = binary_closing(binary_opening(wrapped, iterations=2), iterations=2)[:, pad:-pad]
    n = mask.shape[1]
    regions, count = components(np.hstack([mask, mask]))
    found = []
    for index in range(1, count + 1):
        region = regions == index
        cols = np.nonzero(region.any(0))[0]
        # Each window once, whole: starts in the first copy and touches neither outer edge.
        if cols.min() == 0 or cols.max() == 2 * n - 1 or cols.min() >= n or region.sum() < 60:
            continue
        loop = max(_cell_boundary_loops(region), key=lambda pts: abs(_loop_area(pts)))
        polar = [(rs[0] - AUTO_DR_MM / 2 + row * AUTO_DR_MM, ths[0] + math.radians(col * AUTO_DTH_DEG)) for row, col in loop]
        polar = polar[::max(1, len(polar) // 60)]
        xy = face.to_image(np.array([r for r, _ in polar]), np.array([t for _, t in polar]))
        found.append((float(region.sum()), np.round(xy, 1).tolist()))
    found.sort(key=lambda item: item[0], reverse=True)
    notes = []
    if len(found) > 2 and all(area < .25 * found[1][0] for area, _ in found[2:]):
        notes.append(f"忽略了 {len(found) - 2} 个小孔（面积不到第二大窗口的 1/4，配方无法表达）。")
        found = found[:2]
    return [polygon for _, polygon in found], notes


# ---------------------------------------------------------------------------------------------
# Outline tracing: the windows themselves, not parameters of a spoke model.
#
# Fitting stem/arm parameters cannot reproduce a sculpted fork or a flowing arm, however well it
# scores. So the window outlines are taken from the photo: the sectors' median is split into
# window / material, windows whose view through ends on the dark barrel are extended outward along
# their own edges (the outer part of a deep-concave window shows the barrel, not the background),
# the pattern is made mirror-symmetric about the spoke axis when it is symmetric, smoothed, and
# traced into closed outlines in face mm. The result is an `outline` family recipe.
# ---------------------------------------------------------------------------------------------

TRACE_DTH_DEG, TRACE_XY_MM = .25, .5


def _polar_median(image, p, face, groups, rs, sectors=None):
    from scipy.ndimage import map_coordinates
    pitch = 2 * math.pi / groups
    ths = np.radians(np.arange(0, math.degrees(pitch), TRACE_DTH_DEG))
    rr, tt = np.meshgrid(rs, ths, indexing="ij")
    stack = []
    for k in (range(groups) if sectors is None else sectors):
        xy = face.to_image(rr.ravel(), (tt + k * pitch).ravel())
        stack.append(np.stack([map_coordinates(image[..., c], [xy[:, 1], xy[:, 0]], order=1, mode="nearest").reshape(rr.shape)
                               for c in range(3)], -1))
    stack = np.array(stack)
    return np.median(stack, 0), np.std(stack.mean(-1), 0), ths


def _lug_angle(image, p, face):
    """Angle (rad) of a lug hole near the PCD, and its contrast (0..1), or None.

    The photo's lug circle need not match the spec PCD (HF6-4's shot scales to ~124 of 139.7), so radii
    from 0.8 to 1.1 x PCD/2 are tried; a hole is where the ring differs most from the rings 13 mm
    in and out, averaged over the bolts.
    """
    from scipy.ndimage import map_coordinates
    gray = image[..., :3].mean(-1)
    step = .5
    th = np.radians(np.arange(0, 360, step))
    per = int(round(360 / step / p.bolts))
    if per * p.bolts != len(th):
        return None

    def ring(r):
        xy = face.to_image(np.full_like(th, r), th)
        return map_coordinates(gray, [xy[:, 1], xy[:, 0]], order=1, mode="nearest")

    best = None
    for r in np.arange(p.pcd / 2 * .8, p.pcd / 2 * 1.1, 1.0):
        fold = np.abs(ring(r) - (ring(r - 13) + ring(r + 13)) / 2).reshape(p.bolts, per).mean(0)
        contrast = float(fold.max() - np.median(fold))
        if best is None or contrast > best[1]:
            best = (math.radians(np.argmax(fold) * step), contrast)
    return best


LUG_CONTRAST = .15   # least lug-hole contrast (grey 0..1) for the lugs to pick the spoke axis


def _extend_outward(mask, rs, r_to, min_reach):
    """Continue windows that end on the barrel view outward along their own side edges."""
    from scipy.ndimage import label as components
    out = mask.copy()
    n = mask.shape[1]
    regions, count = components(np.hstack([mask, mask]))
    dr = rs[1] - rs[0]
    for index in range(1, count + 1):
        rows, cols = np.nonzero(regions == index)
        if cols.min() >= n or rs[rows.max()] < min_reach:
            continue
        top = rows.max()
        fit_rows = [row for row in range(top - int(25 / dr), top - int(6 / dr)) if row >= 0 and np.any(rows == row)]
        if len(fit_rows) < 5:
            continue
        lo = np.array([cols[rows == row].min() for row in fit_rows], float)
        hi = np.array([cols[rows == row].max() for row in fit_rows], float)
        a_lo, a_hi = np.polyfit(rs[fit_rows], lo, 1), np.polyfit(rs[fit_rows], hi, 1)
        for row in range(top + 1, len(rs)):
            if rs[row] > r_to:
                break
            c0, c1 = np.polyval(a_lo, rs[row]), np.polyval(a_hi, rs[row])
            if c1 - c0 < 2:
                break
            for c in range(int(round(c0)), int(round(c1)) + 1):
                out[row, c % n] = True
    return out


def _mirror_axis(mask, pitch_cols):
    """Column of the best mirror axis in a periodic sector mask, and its agreement (0..1)."""
    best = (0, -1.0)
    for axis in range(pitch_cols):
        mirrored = np.roll(mask[:, ::-1], 2 * axis + 1, axis=1)
        agree = np.count_nonzero(mirrored & mask) / max(np.count_nonzero(mirrored | mask), 1)
        if agree > best[1]:
            best = (axis, agree)
    return best


def _smooth_loop(points, sigma, keep=120):
    from scipy.ndimage import gaussian_filter1d
    pts = np.asarray(points, float)
    step = np.linalg.norm(np.diff(np.vstack([pts, pts[:1]]), axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(step)])
    even = np.linspace(0, s[-1], max(int(s[-1] / .5), 24), endpoint=False)
    ring = np.vstack([pts, pts[:1]])
    xy = np.column_stack([np.interp(even, s, ring[:, 0]), np.interp(even, s, ring[:, 1])])
    xy = gaussian_filter1d(xy, sigma / .5, axis=0, mode="wrap")
    if keep is None:
        return xy
    return xy[np.linspace(0, len(xy), keep, endpoint=False).astype(int)]


TRACE_CORNER_DEG = 25.0     # a turn sharper than this is a machined corner, not part of a curve
TRACE_STRAIGHT_MM = .8      # Douglas-Peucker tolerance: edges within this of a line are made straight


def _douglas_peucker(pts, tol):
    keep = np.zeros(len(pts), bool)
    keep[[0, -1]] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        ab = b - a
        n = np.hypot(*ab)
        seg = pts[i + 1:j] - a
        d = np.abs(ab[0] * seg[:, 1] - ab[1] * seg[:, 0]) / n if n > 1e-9 else np.hypot(seg[:, 0], seg[:, 1])
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return pts[keep]


def _polygon_loop(points, corner_r=3.0, tol=TRACE_STRAIGHT_MM):
    """Traced window loop as machined geometry: straight edges, corners of radius corner_r, and
    gentle curves kept as curves. Gaussian smoothing alone rounds every corner by the smoothing
    width and leaves traced edges wavy (HF6-4 spokes looked soft next to the photo)."""
    xy = _smooth_loop(points, .75, keep=None)                    # pixel stairs only
    start = int(np.argmax(xy[:, 0]))                             # start on the outermost point (a curve apex, not a corner)
    xy = np.roll(xy, -start, axis=0)
    poly = _douglas_peucker(np.vstack([xy, xy[:1]]), tol)[:-1]
    m = len(poly)
    out = []
    for i in range(m):
        prev, cur, nxt = poly[i - 1], poly[i], poly[(i + 1) % m]
        u, v = cur - prev, nxt - cur
        lu, lv = np.hypot(*u), np.hypot(*v)
        turn = math.degrees(math.atan2(u[0] * v[1] - u[1] * v[0], u @ v))
        if abs(turn) < TRACE_CORNER_DEG or lu < 1e-6 or lv < 1e-6:
            out.append(cur)
            continue
        half = math.radians(abs(turn)) / 2
        r = min(corner_r, .45 * min(lu, lv) / max(math.tan(half), 1e-6))
        back = r * math.tan(half)
        p0, p1 = cur - u / lu * back, cur + v / lv * back
        for s in np.linspace(0, 1, 7):                           # quadratic Bezier: tangent to both edges
            out.append((1 - s) ** 2 * p0 + 2 * (1 - s) * s * cur + s ** 2 * p1)
    out = np.array(out)
    dense = []                                                   # straight runs: a point every 4 mm
    for i in range(len(out)):
        a, b = out[i], out[(i + 1) % len(out)]
        k = max(1, int(np.hypot(*(b - a)) / 4))
        dense += [a + (b - a) * j / k for j in range(k)]
    return np.array(dense)


def see_through_arc(image, base: dict, rim_points, hub_point, bright=.8, step_deg=5.0, smooth_deg=30.0):
    """(start, length) rad of the face angles whose windows show the background in an oblique photo:
    the longest run of angle bins (averaged over +-smooth_deg) at least half as bright as the brightest."""
    from scipy.ndimage import map_coordinates
    p = recipe_from_dict(base)
    face = FaceMap(p, rim_points, hub_point)
    rs = np.arange(p.pcd / 2 + p.seat_d / 2 + 2, p.ring_r - 4, 2.0)
    ths = np.radians(np.arange(0, 360, 1.0))
    rr, tt = np.meshgrid(rs, ths, indexing="ij")
    xy = face.to_image(rr.ravel(), tt.ravel())
    lit = (map_coordinates(image[..., :3].mean(-1), [xy[:, 1], xy[:, 0]], order=1, mode="nearest") > bright).reshape(rr.shape)
    per = int(step_deg)
    share = lit.mean(0).reshape(-1, per).mean(1)
    k = int(round(smooth_deg / step_deg))                  # spokes and windows alternate bin to bin
    share = np.convolve(np.concatenate([share[-k:], share, share[:k]]), np.ones(2 * k + 1) / (2 * k + 1), "same")[k:-k]
    on = share >= .5 * share.max()
    n = len(on)
    best = (0, 0)
    for i in range(n):
        if on[i] and not on[i - 1]:
            k = 0
            while k < n and on[(i + k) % n]:
                k += 1
            best = max(best, (k, i), key=lambda b: b[0])
    if on.all():
        best = (n, 0)
    return math.radians(best[1] * step_deg), math.radians(best[0] * step_deg)


def see_through_sectors(image, base: dict, rim_points, hub_point, groups: int, bright=.8):
    """Spoke groups of an oblique photo whose windows show the background (the rest show the barrel
    inside, as dark as the spokes): those at least half as bright as the brightest group."""
    p = recipe_from_dict({**base, "spokes": groups})
    face = FaceMap(p, rim_points, hub_point)
    rs = np.arange(p.pcd / 2 + p.seat_d / 2 + 2, p.ring_r - 4, 2.0)
    from scipy.ndimage import map_coordinates
    pitch = 2 * math.pi / groups
    ths = np.radians(np.arange(0, math.degrees(pitch), 1.0))
    rr, tt = np.meshgrid(rs, ths, indexing="ij")
    lum = image[..., :3].mean(-1)
    share = []
    for k in range(groups):
        xy = face.to_image(rr.ravel(), (tt + k * pitch).ravel())
        share.append(float((map_coordinates(lum, [xy[:, 1], xy[:, 0]], order=1, mode="nearest") > bright).mean()))
    top = max(share)
    return [k for k, v in enumerate(share) if v >= .5 * top], share


def trace_outlines(image, base: dict, rim_points, hub_point, groups: int, smooth_mm=2.0, corner_r=3.0, sectors=None,
                   grow_mm=0.0):
    """Outline-family recipe traced from the photo. Returns (recipe dict, report dict).

    `sectors`: the spoke groups to read (default all); an oblique photo uses only the groups whose
    windows show the background (see_through_sectors). `grow_mm` widens every window, for the rims
    an oblique view loses behind the spoke walls."""
    from scipy.ndimage import binary_closing, binary_opening, gaussian_filter, map_coordinates, label as components
    from .window_fit import _cell_boundary_loops, _loop_area

    p = recipe_from_dict({**base, "spokes": groups})
    face = FaceMap(p, rim_points, hub_point)
    pitch = 2 * math.pi / groups
    r_min = p.pcd / 2 + p.seat_d / 2 + 1.0              # keep lug seats and the hub out of the trace
    r_lip = p.lip_face_r_in - 2 if p.lip_face_r_in > p.ring_r else p.ring_r - 2
    rs = np.arange(r_min, p.lip_r * .985, 1.0)
    median, spread, ths = _polar_median(image, p, face, groups, rs, sectors)
    labels = _two_means(np.concatenate([median, spread[..., None] * 2], -1).reshape(-1, 4)).reshape(median.shape[:2])
    material = np.bincount(labels[:4].ravel(), minlength=2).argmax()   # the band just outside the lug seats
    n = labels.shape[1]
    pad = 12
    raw = labels != material
    wrapped = np.hstack([raw[:, -pad:], raw, raw[:, :pad]])
    mask = binary_closing(binary_opening(wrapped, iterations=1), iterations=2)[:, pad:-pad]
    mask[rs > p.lip_r * .97] = False
    notes = []
    extended = _extend_outward(mask, rs, r_lip, .7 * p.lip_r)
    if extended.sum() > mask.sum() * 1.02:
        notes.append(f"窗口外段在照片里看到的是轮辋内壁，已沿窗口两侧边外延到 r≈{r_lip:.0f} mm（轮缘内侧）。")
    mask = extended
    axis_col, agree = _mirror_axis(mask, n)
    other = (axis_col + n // 2) % n
    # The spoke axis carries material through the window band; the window axis does not.
    band = (rs > r_min + 10) & (rs < .8 * p.lip_r)
    if mask[band, other].mean() < mask[band, axis_col].mean():
        axis_col, other = other, axis_col
    # The lugs sit half a pitch off the spoke axis in the model (lug_tools); a group with a long centre
    # window (HF6-5's arrow) fools the material test above, so the photo's lugs decide when they show.
    lug = _lug_angle(image, p, face)
    if lug and lug[1] > LUG_CONTRAST:
        lug_pitch = 2 * math.pi / p.bolts
        off = lambda col: abs((lug[0] - ths[0] - math.radians(col * TRACE_DTH_DEG) - pitch / 2 + lug_pitch / 2)
                              % lug_pitch - lug_pitch / 2)
        if off(other) < off(axis_col):
            notes.append("按照片中螺栓孔的位置选了辐条轴（窗口带材料判断选的是另一条镜像轴）。")
            axis_col = other
    level = mask.astype(float)
    if agree > .8:
        level = (level + np.roll(level[:, ::-1], 2 * axis_col + 1, axis=1)) / 2   # thresholded after smoothing
    else:
        notes.append(f"窗口左右不对称（镜像一致度 {agree:.2f}），未做对称化。")
    axis = ths[0] + math.radians(axis_col * TRACE_DTH_DEG)
    # Resample onto a face xy grid (every group from the one median sector), smooth, trace.
    half = p.lip_r
    xs = np.arange(-half, half, TRACE_XY_MM)
    gx, gy = np.meshgrid(xs, xs)
    gr, gt = np.hypot(gx, gy), (np.arctan2(gy, gx) + axis) % pitch
    field = map_coordinates(level, [(gr - rs[0]) / 1.0, gt / math.radians(TRACE_DTH_DEG)], order=1, mode="nearest")
    field[(gr < rs[0]) | (gr > rs[-1])] = 0
    field = gaussian_filter(field, (min(smooth_mm, 1.0) if corner_r > 0 else smooth_mm) / TRACE_XY_MM) > .5
    if grow_mm > 0:
        from scipy.ndimage import distance_transform_edt
        field = (distance_transform_edt(~field) * TRACE_XY_MM <= grow_mm) & (gr >= rs[0]) & (gr <= rs[-1])
    regions, count = components(field)
    outlines, areas = [], []
    for index in range(1, count + 1):
        region = regions == index
        area = region.sum() * TRACE_XY_MM ** 2
        ys_, xs_ = np.nonzero(region)
        centre = math.atan2(gy[ys_, xs_].mean(), gx[ys_, xs_].mean())
        if area < 30 or not (-pitch / 4 <= centre < 3 * pitch / 4):
            continue
        loop = max(_cell_boundary_loops(region), key=lambda pts: abs(_loop_area(pts)))
        pts = np.array([(xs[0] + (c - .5) * TRACE_XY_MM, xs[0] + (r - .5) * TRACE_XY_MM) for r, c in loop])
        pts = _polygon_loop(pts, corner_r) if corner_r > 0 else _smooth_loop(pts, smooth_mm)
        outlines.append([[round(float(math.hypot(x, y)), 2), round(math.degrees(math.atan2(y, x)), 3)] for x, y in pts])
        areas.append(area)
    if not outlines:
        raise ValueError("没有识别到窗口：请检查外圈与中心点。")
    through = [r for w in outlines for r, _ in w if r < p.ring_r - 2]
    recipe = asdict(recipe_from_dict({**asdict(p), "family": "outline", "outlines": outlines,
                                      "window_r_in": round(min(r for w in outlines for r, _ in w), 1),
                                      "window_r_out": round(max(through), 1) if through else p.window_r_out}))
    overlay = []
    for k in range(groups):
        for w in outlines:
            r = np.array([q[0] for q in w])
            t = np.radians([q[1] for q in w]) + axis + k * pitch
            overlay.append(np.round(face.to_image(np.minimum(r, p.ring_r), t), 1).tolist())
    report = {"family": "outline", "groups": groups, "windows_per_group": len(outlines),
              "window_areas_mm2": [round(a) for a in areas], "mirror_agreement": round(agree, 3),
              "axis_deg": round(math.degrees(axis), 3),
              "overlay_windows_px": overlay, "notes": notes, "method": "forged-photo-trace-v1",
              "limits": "窗口平面轮廓取自照片；深度、厚度、侧面斜面与背面来自配方假设。"}
    return recipe, report


# ---------------------------------------------------------------------------------------------
# Depth from an oblique photo, given the planform (stereo-like, weak perspective).
#
# The rim ellipse (lip front, z = 0) gives the scale a / lip_r and the tilt acos(b / a); a point at
# depth z then appears shifted by z * (a / lip_r) * sin(tilt) along the ellipse minor axis, towards
# the side the hub click is on. With that parallax fixed by the camera, the dish depth is no longer
# a free scale: the oblique photo, unwarped onto the face at the right depth, shows the same windows
# as the recipe's planform. What the photo sees are the window rims, which sit face_crown_depth below
# the spoke tops when a face surface is used, so the fitted rim profile is lifted by that depth.
# ---------------------------------------------------------------------------------------------


def _wheel_silhouette(q, xx, yy, lip_r, width, sharp=1.0, rear=1.0):
    """Soft mask of a wheel seen at a tilt: front lip ellipse swept to the rear flange.

    q = (cx, cy, a, tilt, phi): lip centre, semi-major axis in px, tilt, direction of increasing
    depth in the image. Weak perspective: the rear flange is the lip ellipse scaled by `rear`
    (farther away, often a smaller flange) and moved by width * a / lip_r * sin(tilt) along phi.
    With rear = 1 the outline is the same with front and rear swapped (HF6-5's 3/4 shot fitted
    the lip onto the rear flange, 2026-09-26).
    """
    cx, cy, a, t, phi = q
    minor = np.array([math.cos(phi), math.sin(phi)])
    major = np.array([-minor[1], minor[0]])
    b = a * math.cos(t)
    off = minor * a / lip_r * width * math.sin(t)
    best = np.full(xx.shape, np.inf)
    for s in np.linspace(0, 1, 25):
        k = 1 - s * (1 - rear)
        dx, dy = xx - cx - s * off[0], yy - cy - s * off[1]
        best = np.minimum(best, np.hypot((dx * major[0] + dy * major[1]) / (a * k), (dx * minor[0] + dy * minor[1]) / (b * k)))
    return 1 / (1 + np.exp(np.clip((best - 1) * a / sharp, -50, 50)))


REAR_MIN = .8    # least rear-flange / lip scale in the oblique outline model


def fit_oblique_camera(image, p, guess_rim, guess_hub, background=.9):
    """Camera of an oblique product photo from the whole wheel outline (lip, barrel, rear flange).

    Half-silhouette ellipse fits put the tilt at 33.5 deg on the official HF6-4 3/4 photo where the
    outline and the see-through windows both say about 35 deg with a 5 % smaller scale and a moved
    centre: one half of an ellipse leaves centre and minor axis poorly determined. The outline of
    the whole wheel depends only on lip_r and width, not on the dish, so it fixes the camera before
    the dish depth is fitted. Returns (rim points of the front lip ellipse, hub point, report).
    """
    from scipy import ndimage
    fg = ndimage.binary_fill_holes(image[..., :3].min(axis=2) < background)
    rows = np.nonzero(fg.any(axis=1))[0]
    keep = np.zeros_like(fg)
    keep[: int(rows.max() - .03 * (rows.max() - rows.min()))] = True     # floor shadow / reflection
    e = fit_ellipse(guess_rim)
    a0 = max(e["a"], e["b"])
    ang = math.radians(e["angle_deg"]) + (0 if e["a"] < e["b"] else math.pi / 2)
    minor0 = np.array([math.cos(ang), math.sin(ang)])
    if minor0 @ (np.asarray(guess_hub, float) - [e["cx"], e["cy"]]) < 0:
        minor0 = -minor0
    phi0 = math.atan2(minor0[1], minor0[0])

    def fit(step, starts):
        g = fg[::step, ::step].astype(float) * keep[::step, ::step]
        k = keep[::step, ::step]
        yy, xx = np.mgrid[:g.shape[0], :g.shape[1]].astype(float)

        def loss(q):
            q = np.asarray(q, float)
            rear = min(max(q[5], REAR_MIN), 1.0)
            m = _wheel_silhouette([q[0] / step, q[1] / step, q[2] / step, q[3], q[4]], xx, yy, p.lip_r, p.width,
                                  rear=rear) * k
            return 1 - (m * g).sum() / max((m + g - m * g).sum(), 1e-9) + abs(q[5] - rear)
        return min((minimize(loss, s, method="Nelder-Mead", options=dict(xatol=.05, fatol=1e-6, maxiter=800)) for s in starts),
                   key=lambda r: r.fun)
    # Both depth directions: the guess can pick the rear flange for the lip; the smaller rear decides.
    coarse = fit(2, [[e["cx"], e["cy"], a0, math.radians(d), ph, .9] for d in (22, 30, 38) for ph in (phi0, phi0 + math.pi)])
    best = fit(1, [coarse.x])
    cx, cy, a, t, phi = (float(v) for v in best.x[:5])
    if t < 0:
        t, phi = -t, phi + math.pi
    minor = np.array([math.cos(phi), math.sin(phi)])
    major = np.array([-minor[1], minor[0]])
    b = a * math.cos(t)
    rim = [(np.array([cx, cy]) + a * math.cos(s) * major + b * math.sin(s) * minor).tolist()
           for s in np.linspace(0, 2 * math.pi, 24, endpoint=False)]
    hub = (np.array([cx, cy]) + .05 * b * minor).tolist()
    return rim, hub, {"outline_iou": round(1 - float(best.fun), 4), "tilt_deg": round(math.degrees(t), 2),
                      "rear_scale": round(min(max(float(best.x[5]), REAR_MIN), 1.0), 3),
                      "px_per_mm": round(a / p.lip_r, 4), "centre_px": [round(cx, 1), round(cy, 1)]}


def _oblique_camera(p, rim_points, hub_point):
    e = fit_ellipse(rim_points)
    a, b = max(e["a"], e["b"]), min(e["a"], e["b"])
    tilt = math.acos(min(b / a, 1.0))
    ang = math.radians(e["angle_deg"]) + (0 if e["a"] < e["b"] else math.pi / 2)
    minor = np.array([math.cos(ang), math.sin(ang)])
    if minor @ (np.asarray(hub_point, float) - np.array([e["cx"], e["cy"]])) < 0:
        minor = -minor
    return e, tilt, -minor * (a / p.lip_r) * math.sin(tilt)


def _planform_polar(p, rs, ths):
    from .forged_blank import window_mask
    half, res = p.lip_r + 8, .5
    mask = window_mask(p, half, res)
    rr, tt = np.meshgrid(rs, ths, indexing="ij")
    col = np.clip(((rr * np.cos(tt) + half) / res).astype(int), 0, mask.shape[1] - 1)
    row = np.clip(((rr * np.sin(tt) + half) / res).astype(int), 0, mask.shape[0] - 1)
    return mask[row, col]


def fit_depth(image, recipe: dict, rim_points, hub_point, bright=.8):
    """Dish depth (hub_z, ring_z, concavity_exp) of a recipe from an oblique photo of the same wheel.

    Keeps the mounting face (ET) by adjusting web_thick_hub. Returns (recipe dict, report dict).
    Only sectors whose windows are seen through to a bright background count as evidence.
    """
    from scipy.ndimage import map_coordinates
    p0 = recipe_from_dict(recipe)
    e, tilt, shift = _oblique_camera(p0, rim_points, hub_point)
    if math.degrees(tilt) < 12:
        raise ValueError(f"照片倾角只有 {math.degrees(tilt):.0f}°，太接近正视，测不出深度；请用 20–45° 的斜视图。")
    # Window rims sit below the spoke top by the face-surface shoulder or the flank chamfer depth.
    crown = p0.face_crown_depth if p0.face_crown_w > 0 else (p0.flank_depth if p0.flank_w > 0 else 0.0)
    pitch = 2 * math.pi / p0.spokes
    rs = np.arange(p0.pcd / 2 + p0.seat_d / 2 + 2, p0.ring_r - 4, 1.0)
    ths = np.radians(np.arange(0, 360, TRACE_DTH_DEG))
    planform = _planform_polar(p0, rs, ths)
    per = int(round(math.degrees(pitch) / TRACE_DTH_DEG))
    lum = np.mean(image[..., :3], axis=-1)

    def sectors(hub_z, ring_z, exp):
        rim_p = recipe_from_dict({**recipe, "hub_z": hub_z - crown, "ring_z": ring_z - crown, "concavity_exp": exp})
        face = FaceMap(rim_p, rim_points, hub_point)
        face.shift = shift
        out = []
        for k in range(p0.spokes):
            rr, tt = np.meshgrid(rs, ths[:per] + k * pitch, indexing="ij")
            xy = face.to_image(rr.ravel(), tt.ravel())
            out.append(map_coordinates(lum, [xy[:, 1], xy[:, 0]], order=1, mode="nearest").reshape(rr.shape) > bright)
        return out

    wide = np.hstack([planform, planform])

    def agree(win, s):
        ref = wide[:, s % len(ths):s % len(ths) + per]
        return (np.count_nonzero(ref & win) - 2 * np.count_nonzero(win & ~ref)) / max(np.count_nonzero(win), 1)

    def phases(wins, around=None, reach=None):
        """Best planform shift per sector: global search, or +-reach steps around the previous one."""
        out = {}
        for k in used:
            cands = range(0, len(ths), 2) if around is None else range(around[k] - reach, around[k] + reach + 1)
            out[k] = max(cands, key=lambda s_: agree(wins[k], s_))
        return out

    first = sectors(p0.hub_z, p0.ring_z, p0.concavity_exp)
    used = [k for k, win in enumerate(first) if win.mean() >= .08]
    if not used:
        raise ValueError("斜视图里没有能透过窗口看到背景的扇区，无法测深度。")
    base = phases(first)

    def score(hub_z, ring_z, exp, reach=12):
        # The sector phase moves with depth (parallax is along one image direction), so it is
        # re-aligned locally (+-3 deg) at every evaluation; a phase fixed at a wrong starting depth
        # biased the fit by ~8 mm on synthetic tests.
        wins = sectors(hub_z, ring_z, exp)
        local = phases(wins, base, reach)
        vals = [agree(wins[k], local[k]) for k in used]
        return float(np.mean(sorted(vals)[-3:])), local

    grid = []
    for hub_z in np.arange(-110, -9, 6.0):
        for ring_z, exp in ((min(p0.ring_z, 0.0), p0.concavity_exp), (-6.0, 1.0), (-15.0, 1.4)):
            if hub_z < ring_z:
                grid.append((score(hub_z, ring_z, exp)[0], hub_z, ring_z, exp))
    grid.sort(reverse=True)
    best = grid[0]
    for _ in range(2):                                  # refine around the best, re-centre the phases
        base = score(*best[1:], reach=24)[1]
        h0 = best[1]
        fine = []
        for hub_z in np.arange(h0 - 6, h0 + 6.1, 1.5):
            for ring_z in (-20.0, -14.0, -8.0, -3.0, 0.0):
                for exp in (.7, .9, 1.1, 1.35, 1.6, 2.0):
                    if ring_z - crown > 0 or hub_z >= ring_z:
                        continue
                    fine.append((score(hub_z, ring_z, exp)[0], hub_z, ring_z, exp))
        fine.sort(reverse=True)
        best = fine[0]
    coarse_by_hub = {}
    for v, h, *_ in grid:
        coarse_by_hub[h] = max(v, coarse_by_hub.get(h, -9))
    grid = [(v, h) for h, v in coarse_by_hub.items()]
    s_best, hub_z, ring_z, exp = fine[0]
    hub_z, ring_z = min(hub_z + crown, -5.0), min(ring_z + crown, 0.0)
    et = p0.hub_z - p0.web_thick_hub + p0.width / 2
    fitted = {"hub_z": round(hub_z, 1), "ring_z": round(ring_z, 1), "concavity_exp": exp,
              "web_thick_hub": round(hub_z + p0.width / 2 - et, 1)}
    coarse = sorted(grid, reverse=True)
    report = {"tilt_deg": round(math.degrees(tilt), 1), "parallax_px_per_mm": round(float(np.linalg.norm(shift)), 3),
              "sectors_used": used, "score": round(s_best, 3),
              "hub_z_scores": [(round(h, 1), round(v, 3)) for v, h in sorted(grid, key=lambda g: g[1])],
              "hub_z_margin": round(coarse[0][0] - coarse[min(3, len(coarse) - 1)][0], 3),
              "crown_depth_added": crown, "et_kept_mm": round(et, 1), "fitted": fitted,
              "method": "forged-photo-depth-v1 (weak perspective, window rims)",
              "limits": "测的是窗口边缘的深度；辐条顶面 = 边缘 + 斜面深度（face_crown_depth 或 flank_depth，未测）。凹面形状参数把握度低于中心深度。"}
    return {**recipe, **fitted}, report
