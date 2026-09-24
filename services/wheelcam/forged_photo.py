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


def _sector_stack(image, p, face, groups):
    from scipy.ndimage import map_coordinates
    pitch = 2 * math.pi / groups
    rs = np.arange(p.hub_r + 4, p.ring_r - 1, AUTO_DR_MM)
    ths = np.radians(np.arange(0, math.degrees(pitch), AUTO_DTH_DEG))
    rr, tt = np.meshgrid(rs, ths, indexing="ij")
    stack = []
    for k in range(groups):
        xy = face.to_image(rr.ravel(), (tt + k * pitch).ravel())
        stack.append(np.stack([map_coordinates(image[..., c], [xy[:, 1], xy[:, 0]], order=1, mode="nearest").reshape(rr.shape)
                               for c in range(3)], -1))
    return np.array(stack), rs, ths


def auto_group_count(image, base: dict, rim_points, hub_point, candidates=range(3, 13)):
    """Rotational repeat count of the spoke pattern. Returns (count, {n: mean sector spread})."""
    spread = {}
    for n in candidates:
        p = recipe_from_dict({**base, "spokes": n})
        stack, _, _ = _sector_stack(image, p, FaceMap(p, rim_points, hub_point), n)
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
