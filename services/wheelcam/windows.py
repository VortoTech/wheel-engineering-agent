"""Window-method spoke groups (template v10): pure geometry checks, no CAD kernel.

A spoke group is a turned blank minus closed window outlines. Outlines are XY millimetres in the
group-0 frame; the template rotates them by spoke_phase_deg + k·360/spoke_count. They come from
photo fitting and describe the front planform only — depth, draft and back surfaces stay template
assumptions.
"""
import math

import numpy as np

MIN_WEB_MM = 4.0       # narrowest front-outline material allowed between two windows
HUB_KEEP_MM = 1.5      # cutters are clipped this far outside the hub disc
MIN_AREA_MM2 = 30.0
MIN_POINTS = 8


def rotate(points, degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [(x * c - y * s, x * s + y * c) for x, y in points]


def area(points):
    p = np.asarray(points, float)
    return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))


def _cross_pairs(a0, a1, b0, b1):
    """Boolean matrix: segment i of A properly crosses segment j of B."""
    r, s = a1 - a0, b1 - b0
    qp = b0[None] - a0[:, None]
    denom = r[:, None, 0] * s[None, :, 1] - r[:, None, 1] * s[None, :, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[None, :, 1] - qp[..., 1] * s[None, :, 0]) / denom
        u = (qp[..., 0] * r[:, None, 1] - qp[..., 1] * r[:, None, 0]) / denom
    return (np.abs(denom) > 1e-12) & (t > 0) & (t < 1) & (u > 0) & (u < 1)


def _closed(points):
    p = np.asarray(points, float)
    return p, np.roll(p, -1, axis=0)


def self_intersects(points):
    a0, a1 = _closed(points)
    n = len(a0)
    crossing = _cross_pairs(a0, a1, a0, a1)
    idx = np.arange(n)
    near = (np.abs(idx[:, None] - idx[None]) <= 1) | (np.abs(idx[:, None] - idx[None]) == n - 1)
    return bool(np.any(crossing & ~near))


def _inside(points, polygon):
    """Even-odd ray test of each point against a closed polygon."""
    p = np.asarray(points, float)
    a0, a1 = _closed(polygon)
    straddle = (a0[None, :, 1] > p[:, None, 1]) != (a1[None, :, 1] > p[:, None, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        x = a0[None, :, 0] + (p[:, None, 1] - a0[None, :, 1]) * (a1[None, :, 0] - a0[None, :, 0]) / (a1[None, :, 1] - a0[None, :, 1])
    return (np.sum(straddle & (p[:, None, 0] < x), axis=1) % 2) == 1


def _point_segment(points, a0, a1):
    d = a1 - a0
    t = np.clip(((points[:, None] - a0[None]) * d[None]).sum(-1) / np.maximum((d * d).sum(-1), 1e-12)[None], 0, 1)
    gap = points[:, None] - (a0[None] + t[..., None] * d[None])
    return np.hypot(gap[..., 0], gap[..., 1]).min(axis=1)


def separation(first, second):
    """Minimum distance between two closed outlines; 0 when they touch, cross or nest."""
    a, b = np.asarray(first, float), np.asarray(second, float)
    a0, a1 = _closed(a)
    b0, b1 = _closed(b)
    if _cross_pairs(a0, a1, b0, b1).any() or _inside(a[:1], b).any() or _inside(b[:1], a).any():
        return 0.0
    return float(min(_point_segment(a, b0, b1).min(), _point_segment(b, a0, a1).min()))


def check(outlines, count, hub_r, rim_r):
    """Validate window outlines against the turned blank; raise ValueError with the reason.

    Every window must sit between the hub disc and the rim, stay within one group's angle, and keep
    at least MIN_WEB_MM of front-outline material to every other window including rotated copies —
    which also keeps each group connected from hub to rim.
    """
    if not outlines:
        raise ValueError("窗口法需要至少一个窗口轮廓，请先从照片标注拟合。")
    period = 360 / count
    keep = hub_r + HUB_KEEP_MM
    polygons = []
    for number, outline in enumerate(outlines, 1):
        p = np.asarray(outline, float)
        if len(p) < MIN_POINTS:
            raise ValueError(f"窗口 {number} 的轮廓点少于 {MIN_POINTS} 个。")
        radius = np.hypot(p[:, 0], p[:, 1])
        if radius.max() > rim_r:
            raise ValueError(f"窗口 {number} 超出轮辋外缘。")
        if radius.max() < keep + 5:
            raise ValueError(f"窗口 {number} 落在中心盘内。")
        if abs(area(p)) < MIN_AREA_MM2:
            raise ValueError(f"窗口 {number} 面积过小。")
        if self_intersects(p):
            raise ValueError(f"窗口 {number} 的轮廓自相交。")
        angles = np.degrees(np.unwrap(np.arctan2(p[:, 1], p[:, 0])))
        if angles.max() - angles.min() >= period:
            raise ValueError(f"窗口 {number} 的角度跨度超过一组（{period:.1f}°）。")
        polygons.append(p)
    web = math.inf
    for i, p in enumerate(polygons):
        for j in range(i, len(polygons)):
            for k in range(count):
                if j == i and k == 0:
                    continue
                q = np.asarray(rotate(polygons[j], k * period))
                gap = separation(p, q)
                if gap == 0.0:
                    raise ValueError(f"窗口 {i + 1} 与窗口 {j + 1}（第 {k} 组）重叠。")
                # Webs inside the clipped hub zone are hub material, not spoke webs.
                outside_p, outside_q = p[np.hypot(*p.T) > keep], q[np.hypot(*q.T) > keep]
                if len(outside_p) and len(outside_q):
                    gap = min(_point_segment(outside_p, *_closed(q)).min(), _point_segment(outside_q, *_closed(p)).min())
                web = min(web, float(gap))
    if web < MIN_WEB_MM:
        raise ValueError(f"相邻窗口之间的辐条过窄（{web:.1f} mm，最少 {MIN_WEB_MM:g} mm）。")
    return {"window_count": len(polygons), "min_web_mm": round(web, 2) if math.isfinite(web) else None,
            "open_area_mm2": round(sum(abs(area(p)) for p in polygons) * count, 1)}
