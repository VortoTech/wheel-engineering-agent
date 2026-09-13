"""Local geometric candidates for near-front, dark paired-spoke wheels.

No learned classifier, hidden-geometry inference or measurement certification.
All detections are reviewable candidates; weak periodic evidence cannot change a draft.
"""
import math

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates, sobel
from scipy.optimize import least_squares
from scipy.signal import find_peaks

from .models import WheelSpec

ALGORITHM = "radial-edge-pairs-v1"


def detect(photo, spec: WheelSpec, reference_outer_mm=None):
    with Image.open(photo) as original:
        im = original.convert("L")
        im.thumbnail((720, 720))
    gray = np.asarray(im, dtype=float) / 255
    h, w = gray.shape
    g = gaussian_filter(gray, 1.2)
    gx, gy = sobel(g, axis=1) / 8, sobel(g, axis=0) / 8
    mag = np.hypot(gx, gy)
    y, x = np.nonzero(mag > max(float(np.percentile(mag, 91)), 0.035))
    if len(x) < 100:
        raise ValueError("没有足够清晰的轮廓边缘，无法生成可靠候选。请保留人工拟合。")
    strength = mag[y, x]
    nx, ny = gx[y, x] / strength, gy[y, x] / strength
    votes = np.zeros((h, w))
    for radius in np.arange(min(h, w) * .28, min(h, w) * .53, 2):
        for sign in (-1, 1):
            xx, yy = np.rint(x + sign * radius * nx).astype(int), np.rint(y + sign * radius * ny).astype(int)
            keep = (xx >= w * .15) & (xx < w * .85) & (yy >= h * .15) & (yy < h * .85)
            np.add.at(votes, (yy[keep], xx[keep]), np.minimum(strength[keep], .2))
    cy, cx = np.unravel_index(gaussian_filter(votes, 3).argmax(), votes.shape)
    angles = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    radii = np.arange(min(h, w) * .3, min(h, w) * .54)
    xx, yy = cx + radii[:, None] * np.cos(angles), cy + radii[:, None] * np.sin(angles)
    sx, sy = map_coordinates(gx, [yy, xx], order=1), map_coordinates(gy, [yy, xx], order=1)
    radial = np.abs(sx * np.cos(angles) + sy * np.sin(angles))
    profile = np.mean(np.minimum(radial, .15), axis=1)
    peaks, _ = find_peaks(profile, distance=8, prominence=.004)
    if not len(peaks):
        raise ValueError("未找到连续的外圈候选；当前检测需要轮毂完整、接近正面的图片。")
    peak = max(i for i in peaks if profile[i] >= .5 * max(profile[peaks]))
    radius = radii[peak]
    local = np.abs(radii - radius) < radius * .055
    # Select strong, near-circular edges, then robustly fit an axis-aligned ellipse.
    scores = radial[local] * np.exp(-((radii[local, None] - radius) / (radius * .035)) ** 2)
    chosen = scores.argmax(axis=0)
    edge_r = radii[local][chosen]
    accepted = scores[chosen, np.arange(len(angles))] > .025
    if np.mean(accepted) < .55:
        raise ValueError("外圈边缘覆盖不足，无法可靠对齐；不自动修改参数。")
    px, py = cx + edge_r[accepted] * np.cos(angles[accepted]), cy + edge_r[accepted] * np.sin(angles[accepted])
    def residual(p):
        return (np.hypot((px - p[0]) / p[2], (py - p[1]) / p[3]) - 1) * radius
    fit = least_squares(residual, [cx, cy, radius, radius], loss="soft_l1", f_scale=2,
                        bounds=([cx-radius*.08, cy-radius*.08, radius*.88, radius*.88],
                                [cx+radius*.08, cy+radius*.08, radius*1.12, radius*1.12]))
    cx, cy, rx, ry = map(float, fit.x)
    if cx - rx < -2 or cy - ry < -2 or cx + rx > w + 2 or cy + ry > h + 2:
        raise ValueError("外圈候选超出图像边界，可能被裁切；请使用完整轮毂图片。")

    candidates = []
    # The outer band avoids most workshop background and highlights dark spoke ends.
    for count in range(5, 11):
        best = None
        for offset in np.linspace(.045, .115, 15):
            phases = np.deg2rad(np.arange(0, 360 / count, .5))[:, None, None]
            theta = phases + np.arange(count)[None, :, None] * 2 * np.pi / count
            rs = np.array([.80, .82, .84, .86, .88])[None, None, :]
            def sample(u):
                return map_coordinates(g, [cy + ry * (rs*np.sin(theta) + u*np.cos(theta)),
                    cx + rx * (rs*np.cos(theta) - u*np.sin(theta))], order=1, mode="nearest")
            arms = (sample(offset) + sample(-offset)) / 2
            contrast = (sample(0) + sample(offset*1.55) + sample(-offset*1.55)) / 3 - arms
            per_group = np.mean(np.clip(contrast, -.2, .35), axis=2)
            score = per_group.mean(axis=1) - .7*per_group.std(axis=1) - .15*arms.mean(axis=(1, 2))
            index = int(score.argmax())
            result = {"score": float(score[index]), "groups": count, "image_phase_deg": index*.5,
                      "offset_ratio": float(offset), "contrast": float(contrast[index].mean())}
            if best is None or result["score"] > best["score"]:
                best = result
        candidates.append(best)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    margin = best["score"] - candidates[1]["score"]
    reliable = best["score"] > .005 and margin > .025 and best["contrast"] > .08
    theta = math.radians(best["image_phase_deg"]) + np.arange(best["groups"])[:, None] * 2*np.pi/best["groups"]
    stations = []
    for r in (.48, .62, .84):
        u = np.linspace(-.16, .16, 241)
        xx = cx + rx * (r*np.cos(theta) - u*np.sin(theta))
        yy = cy + ry * (r*np.sin(theta) + u*np.cos(theta))
        values = np.median(map_coordinates(g, [yy, xx], order=1, mode="nearest"), axis=0)
        edges = []
        for sign in (-1, 1):
            eligible = np.flatnonzero(np.abs(u - sign*best["offset_ratio"]) < .035)
            index = eligible[np.argmin(values[eligible])]
            lo, hi = max(0, index-24), min(len(u)-1, index+24)
            baseline = min(float(values[lo: index+1].max()), float(values[index: hi+1].max()))
            threshold = (baseline + float(values[index])) / 2
            left = right = int(index)
            while left > lo and values[left-1] < threshold:
                left -= 1
            while right < hi and values[right+1] < threshold:
                right += 1
            edges.extend([float(u[left]), float(u[right])])
        points = [[float(cx + rx*(r*math.cos(t)-v*math.sin(t))),
                   float(cy + ry*(r*math.sin(t)+v*math.cos(t)))] for t in theta[:, 0] for v in edges]
        stations.append({"radius_ratio": r, "edges_ratio": edges, "points": points,
                         "gap_ratio": max(0, edges[2]-edges[1]),
                         "blade_width_ratio": ((edges[1]-edges[0])+(edges[3]-edges[2])) / 2})
    proposed = {"spoke_count": best["groups"],
                "spoke_phase_deg": round((-best["image_phase_deg"]) % (360 / best["groups"]), 2)}
    target_outer = spec.rim_diameter_in * 25.4 + 35
    tip = stations[-1]
    gap, blade = tip["gap_ratio"] * target_outer / 2, tip["blade_width_ratio"] * target_outer / 2
    # Only fit the end widths if the actual detected widths are in the supported CAD range.
    if 16 <= gap <= 50 and 4 <= blade <= 14:
        proposed.update(paired_gap_mm=round(gap, 2), paired_tip_width_mm=round(blade, 2))
    warnings = ["局部边缘候选，未接入通用视觉大模型；适用于完整、近正面、深色双辐图片",
                "外圈椭圆仅作仿射对齐，不代表透视或曲面深度恢复",
                "中段点位可能受背景、遮挡和高光影响；本次只建议组数、相位及可靠的外端宽度",
                "ET、PCD、中心孔、背面厚度、材料和连接方式不由本次识图推断"]
    can_apply = reliable and spec.spoke_style == "paired"
    if not reliable:
        warnings.insert(0, "周期特征不够明确，仅展示候选，不允许自动套用")
    if spec.spoke_style != "paired":
        warnings.insert(0, "当前为单辐项目；请新建双辐项目后再拟合，避免覆盖结构类型")
    try:
        WheelSpec.model_validate({**spec.model_dump(), **proposed})
    except ValueError:
        can_apply = False
        warnings.insert(0, "候选尺寸不满足当前 CAD 约束，请人工调整，不能直接套用")
    return {"algorithm": ALGORITHM, "status": "candidates" if reliable else "ambiguous",
            "image_size": [w, h], "ellipse": {"cx": cx, "cy": cy, "rx": rx, "ry": ry},
            "edge_coverage": round(float(np.mean(accepted)), 3),
            "edge_residual_px": round(float(np.median(np.abs(residual(fit.x)))), 2),
            "outer_points": list(map(list, zip(px[::8].tolist(), py[::8].tolist()))),
            "spokes": best, "alternatives": candidates[1:3], "score_margin": margin,
            "stations": stations, "suggested_parameters": proposed, "can_apply": can_apply,
            "scale": {"reference_outer_mm": reference_outer_mm, "target_outer_mm": target_outer,
                      "reference_gap_mm": tip["gap_ratio"]*reference_outer_mm/2 if reference_outer_mm else None,
                      "basis": "参考实物外径为用户输入、尚未核验；候选参数按当前目标模型外径映射" if reference_outer_mm else "无实测标尺；仅将比例映射到当前模型的假设外径"},
            "warnings": warnings}
