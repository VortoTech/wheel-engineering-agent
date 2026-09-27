"""Wheel Engineering Skill: photos + known specs -> provenance-tagged recipe -> CAD -> verification report.

Every parameter carries where it came from (user, spec, photo, estimate, default) and, for photo
measurements, a confidence from the fit that produced it. Nothing unobservable is guessed silently:
it is either an explicit default listed under `unknown`, or the run stops and says what to provide.

    python -m wheelcam.wheel_skill --front front.jpg [--oblique angle.jpg] \
        --spec '{"diameter_in": 20, "width_in": 9.5, "pcd_mm": 139.7, "bolts": 6, "center_bore_mm": 106.1, "et_mm": 30}' \
        --out runs/hf64 [--no-build]

Readiness levels (the report never claims more than the evidence supports):
  L0 visual        a shape that looks like the photo
  L1 parametric    a valid, editable single-solid STEP from a recipe
  L2 dimensioned   every key dimension from the user or a spec, and verified on the solid
  L3 validated     L2 + geometry integrity, bolt pattern, symmetry and fit checks all pass
  L4 / L5          simulation / manufacturing review: out of scope, never assigned here
"""
import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .forged_blank import ForgedWheel, recipe_from_dict

KEY_SPECS = {                      # spec key -> what it fixes; all are needed for L2
    "diameter_in": "rim diameter (lip radius)",
    "width_in": "rim width (overall width)",
    "pcd_mm": "bolt circle diameter",
    "bolts": "bolt count",
    "center_bore_mm": "centre bore",
    "et_mm": "offset ET (mounting face)",
}
BORE_WALL = 3.0                    # least wall between a lug seat counterbore and the centre bore, mm
WEB_HUB = (35.0, 60.0)             # hub web thickness a forged centre is given, mm
RING_Z_MAX = -5.0                  # the spoke ring stays this far under the lip face, mm
SEAT_D = 30.0                      # lug seat counterbore, mm: a 22 mm hex socket (~28 mm OD) plus clearance
UNOBSERVABLE = {                   # never measured from photos; listed with the value used
    "back_side": "背面减重腔、背部结构：照片看不到，按模板默认（当前关闭）。",
    "spoke_chamfer_depth": "辐条斜切深度：照片测不到，按默认值。",
    "wall_thickness": "轮辋壁厚、辐条前后厚度：照片测不到，按模板默认。",
    "material": "材料与热处理：按 6061-T6 估重，未验证。",
}


def _record(value, source, confidence=None, note=""):
    return {"value": value, "source": source, "confidence": confidence, "note": note}


def envelope_from_specs(spec: dict) -> tuple[dict, dict]:
    """Rim / hub envelope from the known specs. Returns (recipe updates, provenance)."""
    base = ForgedWheel()
    upd, prov = {}, {}
    if "diameter_in" in spec:
        lip_r = spec["diameter_in"] * 25.4 / 2 + 18.0            # bead seat radius + flange height
        upd.update(lip_r=round(lip_r, 1), lip_face_r_in=round(lip_r - 14, 1), barrel_outer_r=round(lip_r - 22, 1),
                   barrel_inner_r=round(lip_r - 27, 1), ring_r=round(lip_r - 38, 1))
        prov["lip_r"] = _record(upd["lip_r"], "spec", 1.0, f'{spec["diameter_in"]}″ 名义直径 + 轮缘 18 mm（轮缘高度为模板估计）')
        prov["barrel_wall"] = _record(5.0, "estimate", None, "旋压轮辋常见壁厚 4–6 mm")
    else:
        prov["lip_r"] = _record(base.lip_r, "default", None, "未提供直径，用模板默认 20″")
    if "width_in" in spec:
        upd["width"] = round(spec["width_in"] * 25.4 + 25.0, 1)    # J width + two flange thicknesses
        prov["width"] = _record(upd["width"], "spec", 1.0, f'{spec["width_in"]}J + 两侧轮缘约 25 mm（估计）')
    else:
        prov["width"] = _record(base.width, "default", None, "未提供宽度")
    for key, field, fn in (("pcd_mm", "pcd", float), ("bolts", "bolts", int)):
        if key in spec:
            upd[field] = fn(spec[key])
            prov[field] = _record(upd[field], "spec", 1.0)
    if "center_bore_mm" in spec:
        upd["center_bore_r"] = round(spec["center_bore_mm"] / 2, 2)
        prov["center_bore_r"] = _record(upd["center_bore_r"], "spec", 1.0)
    if "pcd_mm" in spec:
        upd["hub_r"] = round(spec["pcd_mm"] / 2 + 10, 1)
        prov["hub_r"] = _record(upd["hub_r"], "estimate", None, "PCD/2 + 10 mm")
    # Lug seat counterbore vs centre bore: 6 x 139.7 with a 106.1 bore leaves 16.8 mm from lug centre
    # to bore wall, and the template's 40 mm seat cut 3 mm into the bore (HF6-4, 2026-09-25).
    # The template's 40 mm also dwarfed the lugs where the bore left room (HF6-5, 2026-09-26): start
    # from SEAT_D, a 22 mm hex socket's wall plus clearance.
    room = upd.get("pcd", base.pcd) / 2 - upd.get("center_bore_r", base.center_bore_r) - BORE_WALL
    upd["seat_d"] = min(SEAT_D, math.floor(room * 4) / 2)
    note = f"M14 螺母 22 mm 套筒外径加间隙 {SEAT_D} mm"
    if upd["seat_d"] < SEAT_D:
        note = f"{SEAT_D} mm 会切进中心孔，缩到中心孔壁留 {BORE_WALL} mm"
    prov["seat_d"] = _record(upd["seat_d"], "estimate", None, note + "；需按螺母规格确认")
    return upd, prov


def _front_rim_hub(image):
    """Rim circle and hub of a straight-on photo on a light background (None if not straight-on)."""
    fg = image[..., :3].min(axis=2) < .85
    fg[int(fg.shape[0] * .97):] = False                     # ignore a floor shadow line at the bottom
    ys, xs = np.nonzero(fg)
    if len(xs) < 1000:
        return None
    w, h = xs.max() - xs.min(), ys.max() - ys.min()
    if not .93 < w / max(h, 1) < 1.07:                       # an oblique view is an ellipse, not a circle
        return None
    cx, cy, r = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, (w + h) / 4
    rim = [[cx + r * math.cos(t), cy + r * math.sin(t)] for t in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
    return rim, [cx, cy]


def _oblique_rim_hub(image):
    """Lip ellipse of an oblique product photo: fit on the silhouette half that is not the barrel."""
    from .window_fit import fit_ellipse
    fg = image[..., :3].min(axis=2) < .9
    ys, xs = np.nonzero(fg)
    cx0, cy0 = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    r0 = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2
    halves = {}
    for side in (1, -1):                                    # right / left half-silhouettes
        pts = []
        for a in np.radians(np.arange(-80, 81, 4)):
            ang = a if side > 0 else math.pi - a
            for rr in np.arange(r0 * 1.3, r0 * .4, -.5):
                x, y = cx0 + rr * math.cos(ang), cy0 + rr * math.sin(ang)
                if 0 <= int(y) < fg.shape[0] and 0 <= int(x) < fg.shape[1] and fg[int(y), int(x)]:
                    pts.append((x, y))
                    break
        halves[side] = pts
    # The two half-silhouettes are the front lip and the rear rim: two ellipses of about the same
    # size, offset by the rim width. What is inside only the lip ellipse is the edge of the spoke
    # face (background shows through the windows); what is inside only the rear-rim ellipse is the
    # outside of the barrel (solid). Cues that failed on HF6-4: half extents (equal, the barrel
    # shifts the bounding-box centre), background just inside each half, and closing each ellipse
    # (both closed ellipses stay inside the silhouette).
    fits = {s: fit_ellipse(p) for s, p in halves.items() if len(p) >= 5}
    yy, xx = np.mgrid[:fg.shape[0], :fg.shape[1]]

    def inside(e):
        phi = math.radians(e["angle_deg"])
        u = (xx - e["cx"]) * math.cos(phi) + (yy - e["cy"]) * math.sin(phi)
        v = -(xx - e["cx"]) * math.sin(phi) + (yy - e["cy"]) * math.cos(phi)
        return (u / e["a"]) ** 2 + (v / e["b"]) ** 2 <= 1

    masks = {s: inside(e) for s, e in fits.items()}

    def background_in_own_crescent(s):
        own = masks[s].copy()
        for t, m in masks.items():
            if t != s:
                own &= ~m
        return (~fg[own]).mean() if own.any() else 0.0
    lip = max(fits, key=background_in_own_crescent)
    e = fits[lip]
    phi = math.radians(e["angle_deg"])
    rim = [[e["cx"] + e["a"] * math.cos(t) * math.cos(phi) - e["b"] * math.sin(t) * math.sin(phi),
            e["cy"] + e["a"] * math.cos(t) * math.sin(phi) + e["b"] * math.sin(t) * math.cos(phi)]
           for t in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
    hub = [e["cx"] - lip * .05 * e["b"], e["cy"]]           # only the side matters: the hub sits toward the barrel
    return rim, hub


STYLE_KEYS = ("center_pad", "arm_groove", "hub_valleys")
# "forged_y" preset (HF6-4 benchmark, 2026-09-26): the numbers the rules below reproduce on that wheel.
FORGED_Y = dict(flank_w=16.0, flank_depth=22.0, flank_share=.4, face_chamfer=2.0, window_pocket_depth=60.0,
                spoke_pad_depth=10.0, spoke_pad_share=.45, groove_w=4.0, groove_depth=3.0,
                hub_valley_depth=16.0, hub_valley_draft_deg=35.0)


def style_features(recipe: dict, style: dict | None = None) -> tuple[dict, dict, list]:
    """Style features of an outline-family recipe: which ones (from `style`, e.g. a VLM or the user,
    else the forged_y preset, asked about) and their sizes, from rules on the traced geometry.

    Rules (fitted on the HF6-4 benchmark, to be checked on other wheels):
      hub crease   r = window_r_in - 15, rising 0.58 mm/mm from bore + 6; the dish is straight beyond it
      spoke pads   width 2 x 0.7 x the stem half width, from the crease to ring_r - 9
      arm grooves  from the fork + 8 to 8 inside the lip face
      hub valleys  arms pad width + 2 wide at the hub
    Returns (recipe updates, provenance, questions).
    """
    import numpy as np
    from dataclasses import replace
    from .forged_blank import PAD_MIN_HALF, _valley_radii, face_z, recipe_from_dict as rfd, spoke_centrelines
    p = rfd(recipe)
    upd, prov, questions = dict(FORGED_Y), {}, []
    given = {k: (style or {}).get(k) for k in STYLE_KEYS}
    source = (style or {}).get("source", "user") if style else None
    on = {k: (v if v is not None else True) for k, v in given.items()}
    if any(v is None for v in given.values()):
        questions.append("造型特征按「锻造 Y 辐」预设（辐条中心凸台、臂上沟槽、中心谷）建模：照片里是否有这些特征？可逐项关闭。")
    for k in STYLE_KEYS:
        prov[k] = _record(on[k], source if given[k] is not None else "default", None,
                          "由视觉模型/用户判断" if given[k] is not None else "forged_y 预设，待确认")
    lines = spoke_centrelines(p)
    wide = [(line, hw) for line, hw in lines if np.median(hw) >= PAD_MIN_HALF]
    radii = [np.hypot(*line.T) for line, _ in wide]
    stems = [np.median(hw) for (line, hw), r in zip(wide, radii) if r.min() < p.window_r_in + 10]
    arms = [float(r.min()) for r in radii if r.min() > p.window_r_in + 30 and r.max() > p.ring_r - 20]   # reach the rim
    stem_half = float(max(stems)) if stems else 15.0
    hub_r = p.center_bore_r + 6
    crease_r = max(p.window_r_in - 15, hub_r + 10)
    crease_z = p.hub_z + .58 * (crease_r - hub_r)
    upd.update(hub_r=round(hub_r, 1), hub_crease_r=round(crease_r, 1), hub_crease_z=round(min(crease_z, p.ring_z - 5), 1),
               concavity_exp=1.0)
    prov["hub_crease"] = _record({"r": upd["hub_crease_r"], "z": upd["hub_crease_z"]}, "rule", None,
                                 "窗口起点内 15 mm，自中心孔外 6 mm 起 0.58 坡度（HF6-4 标定）")
    pad_w = round(2 * .7 * stem_half, 1)
    if on["center_pad"]:
        upd.update(spoke_pad_w=pad_w, spoke_pad_r=[round(crease_r + 2, 1), round(p.ring_r - 9, 1)])
        prov["spoke_pad"] = _record({"w": pad_w, "r": upd["spoke_pad_r"]}, "rule", None, f"主辐条半宽 {stem_half:.1f} mm 的 0.7")
    if on["arm_groove"] and arms:
        upd["outline_groove_r"] = [round(min(arms) + 8, 1), round(p.lip_face_r_in - 8, 1)]
        prov["arm_groove"] = _record(upd["outline_groove_r"], "rule", None, "分叉后 8 mm 到轮唇内 8 mm")
    if on["hub_valleys"]:
        upd["hub_arm_w"] = round(pad_w + 2, 1)
        radii = _valley_radii(replace(p, hub_arm_w=upd["hub_arm_w"], hub_valley_depth=upd["hub_valley_depth"],
                                      hub_valley_draft_deg=upd["hub_valley_draft_deg"]))
        if radii is None:
            on["hub_valleys"] = False
            prov["hub_valleys"] = _record(False, "rule", None, "辐条根部放不下谷底")
            questions.append("辐条根部太挤，放不下中心谷，已省略。")
        else:
            prov["hub_valleys"] = _record({"arm_w": upd["hub_arm_w"], "depth": upd["hub_valley_depth"], "r": [round(x, 1) for x in radii]},
                                          "rule", None, "凸台宽 + 2 mm；谷底放得下圆角处起")
    if not on["hub_valleys"]:
        upd["hub_valley_depth"] = 0.0
    return upd, prov, questions


def _hold_hub_web(recipe: dict) -> str | None:
    """Keep the hub web (hub face to mounting face, set by ET) within WEB_HUB by moving the hub face.

    The photo's dish depth is overruled when it leaves the web outside forging practice: HF6-5's 3/4
    shot gave 23.5 mm (lug seats nearly through) with one camera and 85 mm (a flat face) with
    another (2026-09-26). The spoke ring stays above the hub and at least RING_Z_MAX under the lip.
    Returns a note when it changed anything.
    """
    web = recipe["web_thick_hub"]
    if WEB_HUB[0] <= web <= WEB_HUB[1]:
        return None
    fixed = min(max(web, WEB_HUB[0]), WEB_HUB[1])
    recipe["hub_z"] = round(recipe["hub_z"] + fixed - web, 1)
    recipe["web_thick_hub"] = fixed
    recipe["ring_z"] = min(max(recipe["ring_z"], recipe["hub_z"] + 10), RING_Z_MAX)
    return f'斜视图测得的凹面深度会让中心盘厚 {web} mm，超出锻造常用 {WEB_HUB[0]:.0f}–{WEB_HUB[1]:.0f} mm，已按 {fixed:.0f} mm 建模'


def reconstruct(front_image, spec: dict, oblique_image=None, front_rim_hub=None, oblique_rim_hub=None,
                chamfer=(9.5, 10.0), style=None):
    """Recipe + provenance + questions. Raises nothing for missing specs: they are reported as questions."""
    from .forged_photo import auto_group_count, fit_depth, trace_outlines
    questions, prov = [], {}
    missing = [k for k in KEY_SPECS if k not in spec]
    for k in missing:
        questions.append(f"请提供 {KEY_SPECS[k]}（{k}）：照片无法可靠确定。")
    upd, prov_env = envelope_from_specs(spec)
    prov.update(prov_env)
    if prov.get("seat_d", {}).get("source") == "estimate":
        questions.append(f'螺母座让位孔按 {upd["seat_d"]} mm 建模（{prov["seat_d"]["note"]}）：请提供螺母规格'
                         '（锥座/球座、座面直径、套筒外径），确认这个尺寸能装。')
    base = {**asdict(ForgedWheel()), **upd}

    rim_hub = front_rim_hub or _front_rim_hub(front_image)
    if rim_hub is None:
        raise ValueError("正面图不是正视图（外圈不是圆）：请提供正视图，或给出外圈与中心点击点。")
    rim, hub = rim_hub
    groups, spreads = auto_group_count(front_image, base, rim, hub)
    vals = sorted(spreads.values())
    baseline = float(np.median(vals))
    group_conf = round(max(0.0, min(1.0, (baseline - spreads[groups]) / max(baseline - vals[0], 1e-9))), 2)
    prov["spokes"] = _record(groups, "photo", group_conf, "扇区一致性：1 = 与最一致的组数一样好")
    recipe, trace = trace_outlines(front_image, base, rim, hub, groups)
    prov["planform"] = _record(f'{trace["windows_per_group"]} 个窗口/组', "photo", round(float(trace["mirror_agreement"]), 2),
                               "窗口轮廓取自正面照；置信度 = 镜像一致度")
    recipe.update(flank_w=chamfer[0], flank_depth=chamfer[1], lip_pockets=0)
    prov["spoke_chamfer"] = _record({"width": chamfer[0], "depth": chamfer[1]}, "estimate", None, UNOBSERVABLE["spoke_chamfer_depth"])

    et = spec.get("et_mm")
    if oblique_image is not None:
        from .forged_photo import fit_oblique_camera
        o_rim, o_hub = oblique_rim_hub or _oblique_rim_hub(oblique_image)
        o_rim, o_hub, camera = fit_oblique_camera(oblique_image, recipe_from_dict(recipe), o_rim, o_hub)
        prov["oblique_camera"] = _record(camera, "photo", camera["outline_iou"], "整轮外轮廓（前唇边 + 轮筒 + 后轮缘）拟合的相机；依赖直径与宽度")
        recipe, depth = fit_depth(oblique_image, recipe, o_rim, o_hub)
        margin = depth["hub_z_margin"]
        prov["dish_depth"] = _record({k: depth["fitted"][k] for k in ("hub_z", "ring_z", "concavity_exp")}, "photo",
                                     round(min(1.0, margin / .05), 2),
                                     f'斜视图倾角 {depth["tilt_deg"]}°，窗口吻合 {depth["score"]}，峰值差 {margin}（越大越确定）')
    else:
        prov["dish_depth"] = _record({"hub_z": recipe["hub_z"]}, "default", None, "没有斜视图：凹面深度用模板默认")
        questions.append("请提供一张 20–45° 的斜视图：正面照测不出凹面深度。")
    if et is not None:
        recipe["web_thick_hub"] = round(recipe["hub_z"] + recipe["width"] / 2 - et, 1)
        prov["et"] = _record(et, "spec", 1.0, "由 ET 反推安装面位置（中心背面厚度随之调整）")
        note = _hold_hub_web(recipe)
        if note:
            prov["dish_depth"] = _record({"hub_z": recipe["hub_z"], "ring_z": recipe["ring_z"]}, "rule", None, note)
            questions.append(f'{note}（ET {et}）：请核对 ET，或提供更清晰的斜视图 / 凹面深度。')
    recipe["ring_z"] = min(recipe["ring_z"], RING_Z_MAX)
    if style is not False:                         # False: plain traced wheel (no style features)
        s_upd, s_prov, s_q = style_features(recipe, style)
        recipe.update(s_upd); prov.update(s_prov); questions += s_q
    unknown = dict(UNOBSERVABLE)
    return recipe_from_dict(recipe), prov, questions, unknown, {"trace": {k: trace[k] for k in ("windows_per_group", "mirror_agreement", "notes")}}


def verify(step_path, recipe, spec: dict, report: dict) -> dict:
    """Checks on the built solid against the specs and the recipe; returns named PASS/FAIL records."""
    import cadquery as cq
    part = cq.importers.importStep(str(step_path)).val()
    checks = {}
    bb = part.BoundingBox()
    checks["single_valid_solid"] = {"pass": part.isValid() and len(part.Solids()) == 1}
    if "diameter_in" in spec:
        checks["outer_diameter"] = {"pass": abs(bb.xlen - 2 * recipe.lip_r) < .5, "measured_mm": round(bb.xlen, 2),
                                    "expected_mm": round(2 * recipe.lip_r, 2)}
    if "width_in" in spec:
        checks["overall_width"] = {"pass": abs(bb.zlen - recipe.width) < .5, "measured_mm": round(bb.zlen, 2),
                                   "expected_mm": recipe.width}
    # Bolt holes: through cylinders of the bolt diameter, centred on the PCD, evenly spaced.
    holes = []
    for f in part.Faces():
        if f.geomType() == "CYLINDER":
            cyl = f._geomAdaptor().Cylinder()
            if abs(2 * cyl.Radius() - recipe.bolt_d) < .05:
                # The axis, not the face centre: a hole split by a sector seam is two half cylinders
                # whose centres sit off the axis (HF6-2, 2026-09-26).
                c = cyl.Location()
                holes.append((math.hypot(c.X(), c.Y()), math.degrees(math.atan2(c.Y(), c.X())) % 360))
    angles = sorted({round(a, 1) for _, a in holes})
    spacing_ok = len(angles) == recipe.bolts and all(
        abs(((angles[(i + 1) % len(angles)] - angles[i]) % 360) - 360 / recipe.bolts) < .2 for i in range(len(angles)))
    checks["bolt_pattern"] = {"pass": spacing_ok and all(abs(2 * r - recipe.pcd) < .1 for r, _ in holes),
                              "holes_found": len(angles), "expected": recipe.bolts, "pcd_mm": recipe.pcd}
    small = [f.Area() for f in part.Faces() if f.Area() < 5]
    checks["no_sliver_faces"] = {"pass": not small, "faces_under_5mm2": len(small)}
    et = report.get("derived", {}).get("offset_et_mm")
    if "et_mm" in spec and et is not None:
        checks["offset_et"] = {"pass": abs(et - spec["et_mm"]) < .5, "measured_mm": et, "expected_mm": spec["et_mm"]}
    # Rotational symmetry: volume in each spoke sector (the part is built by rotation, so any
    # asymmetric boolean failure shows up as a sector that differs).
    # Lugs break the spoke symmetry unless the counts share a factor (8 groups, 5 lugs: none left).
    order = math.gcd(recipe.spokes, recipe.bolts)
    pitch = 360 / max(order, 1)
    wedge = cq.Solid.makeCylinder(recipe.lip_r + 5, 400, cq.Vector(0, 0, -300))
    vols = []
    for k in range(min(order, 3) if order >= 2 else 0):
        a0 = math.radians(k * pitch - pitch / 2)
        cut = cq.Workplane("XY").workplane(offset=-300).moveTo(0, 0).lineTo(600 * math.cos(a0), 600 * math.sin(a0)) \
            .lineTo(600 * math.cos(a0 + math.radians(pitch)), 600 * math.sin(a0 + math.radians(pitch))).close().extrude(400).val()
        vols.append(part.intersect(cut.intersect(wedge)).Volume())
    if vols:
        spread = (max(vols) - min(vols)) / max(np.mean(vols), 1)
        checks["rotational_symmetry"] = {"pass": spread < .01, "sector_volume_spread": round(float(spread), 4)}
    else:
        checks["rotational_symmetry"] = {"pass": True, "sector_volume_spread": None,
                                         "note": f"{recipe.spokes} 组辐条与 {recipe.bolts} 个螺栓孔没有共同的旋转对称"}
    for record in checks.values():
        record["pass"] = bool(record["pass"])             # numpy bools would serialise as "True"
    return checks


def readiness(prov: dict, checks: dict, spec: dict, built: bool) -> tuple[str, list]:
    """Highest level the evidence supports, with the reasons it is not higher."""
    why = []
    if not built or not checks.get("single_valid_solid", {}).get("pass"):
        return "L0", ["没有生成有效实体。"]
    level = "L1"
    missing = [k for k in KEY_SPECS if k not in spec]
    dim_checks = [k for k in ("outer_diameter", "overall_width", "offset_et", "bolt_pattern") if k in checks]
    if missing:
        why.append("缺少关键尺寸：" + "、".join(KEY_SPECS[k] for k in missing))
    elif not all(checks[k]["pass"] for k in dim_checks):
        why.append("关键尺寸在实体上核对未通过：" + "、".join(k for k in dim_checks if not checks[k]["pass"]))
    else:
        level = "L2"
        failed = [k for k, v in checks.items() if not v["pass"]]
        if failed:
            why.append("几何/约束检查未通过：" + "、".join(failed))
        else:
            level = "L3"
    why.append("L4/L5（强度仿真、制造评审）不在本 Skill 范围内：不可直接用于制造。")
    return level, why


def run(front, spec, out, oblique=None, build=True, kernel="mesh"):
    """kernel "mesh": manifold3d build (seconds; GLB, mass and checks); "brep": the OCC build with a STEP
    for manufacturing (tens of minutes, and OCC booleans failed on most eval-set wheels, 2026-09-26)."""
    from PIL import Image
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    load = lambda path: np.asarray(Image.open(path).convert("RGB"), float) / 255
    recipe, prov, questions, unknown, evidence = reconstruct(load(front), spec, load(oblique) if oblique else None)
    (out / "recipe.json").write_text(json.dumps(asdict(recipe), ensure_ascii=False, indent=1))
    checks, report, mass = {}, {}, None
    if build and kernel == "mesh":
        from . import mesh_build
        (out / "cad").mkdir(parents=True, exist_ok=True)
        body, report = mesh_build.build(recipe)
        mesh_build.export_glb(body, out / "cad" / "wheel.glb")
        (out / "cad" / "report.json").write_text(json.dumps(report, indent=1))
        checks, mass = mesh_build.verify(body, recipe, spec), report["mass_kg_6061"]
    elif build:
        from .forged_blank import export_model
        report = export_model(asdict(recipe), out / "cad", {"forged": asdict(recipe)})
        checks = verify(out / "cad" / "wheel.step", recipe, spec, report)
        mass = report.get("forged", {}).get("part_mass_kg_6061")
    level, why = readiness(prov, checks, spec, build)
    result = {"skill": "wheel-engineering-v0.1", "inputs": {"front": str(front), "oblique": str(oblique) if oblique else None, "spec": spec},
              "readiness": level, "readiness_limits": why, "questions": questions, "parameters": prov, "unknown": unknown,
              "checks": checks, "evidence": evidence,
              "kernel": kernel if build else None, "mass_kg_6061": mass,
              "artifacts": {k: str(out / "cad" / k) for k in ("wheel.step", "wheel.glb") if (out / "cad" / k).exists()} if build else {}}
    (out / "engineering_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Wheel Engineering Skill: photo(s) + specs -> verified parametric CAD")
    ap.add_argument("--front", required=True)
    ap.add_argument("--oblique")
    ap.add_argument("--spec", default="{}", help="JSON: diameter_in, width_in, pcd_mm, bolts, center_bore_mm, et_mm")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--kernel", choices=("mesh", "brep"), default="mesh", help="mesh: seconds, GLB; brep: STEP, slow")
    a = ap.parse_args()
    res = run(a.front, json.loads(a.spec), a.out, a.oblique, not a.no_build, a.kernel)
    print(json.dumps({k: res[k] for k in ("readiness", "readiness_limits", "questions", "checks", "mass_kg_6061")},
                     ensure_ascii=False, indent=1, default=str))
