"""Circular rim / regular lug-pattern camera hypotheses, not metric calibration.

Only structural anchors are read. Spoke boundaries and validation observations
must never take part in camera fitting or candidate selection.
"""
import math

import numpy as np
from scipy.optimize import least_squares


def rotation(theta, phi):
    a = [np.cos(theta)*np.cos(phi), -np.sin(phi), np.sin(theta)*np.cos(phi)]
    b = [np.cos(theta)*np.sin(phi), np.cos(phi), np.sin(theta)*np.sin(phi)]
    return np.array([a, b, np.cross(a, b)])


def project(points, pose):
    q = np.asarray(points) @ np.asarray(pose["rotation"]).T
    distance = pose.get("distance_radii", 1e6)
    denominator = np.ones(len(q)) if distance >= 1e5 else 1-q[:, 2]/distance
    if np.any(denominator <= 0):
        raise ValueError("曲面位于假设相机后方。")
    return np.column_stack([pose["cx"]+pose["scale"]*q[:, 0]/denominator,
                            pose["cy"]-pose["scale"]*q[:, 1]/denominator])


def ring_residual(points, pose):
    r, s, d = np.asarray(pose["rotation"]), pose["scale"], pose["distance_radii"]
    cx, cy = pose["cx"], pose["cy"]
    d = np.inf if d >= 1e5 else d
    h = np.array([[s*r[0, 0]-cx*r[2, 0]/d, s*r[0, 1]-cx*r[2, 1]/d, cx],
                  [-s*r[1, 0]-cy*r[2, 0]/d, -s*r[1, 1]-cy*r[2, 1]/d, cy],
                  [-r[2, 0]/d, -r[2, 1]/d, 1]])
    inverse = np.linalg.inv(h)
    conic = inverse.T @ np.diag([1., 1., -1.]) @ inverse
    p = np.column_stack([points, np.ones(len(points))])
    # First-order geometric distance to the projected circle's conic.
    return np.einsum("ij,ij->i", p @ conic, p)/np.maximum(np.linalg.norm(2*(p @ conic)[:, :2], axis=1), 1e-12)


def fit_candidates(data):
    rim = np.asarray(data["rim_points"], float)
    lugs = np.asarray(data["structure"]["lug_centers"], float)
    hub = np.asarray(data["hub_center"], float)
    if len(rim) < 10 or len(lugs) < 4 or not np.isfinite(np.r_[rim.ravel(), lugs.ravel(), hub]).all():
        raise ValueError("相机研究需要至少十个外圈点和四个按顺时针排列的孔位中心。")
    low, high = rim.min(0), rim.max(0)
    center, scale = (low+high)/2, float(np.max(high-low)/2)
    if scale <= 0:
        raise ValueError("外圈标注尺寸无效。")
    # Scale and translation equivariant; no sample-specific pixel bounds.
    lug_phase = math.atan2(hub[1]-lugs[0, 1], lugs[0, 0]-hub[0])
    initial = [*center, scale, .45, .05, .35, .21, lug_phase]
    lower = [*(center-scale*.4), scale*.65, .03, -.7, .01, .05, lug_phase-.7]
    upper = [*(center+scale*.4), scale*1.4, 1.15, .7, .9, .45, lug_phase+.7]
    candidates = []
    for distance in [3., 5., 8., 12., 1e6]:
        def unpack(p):
            return {"cx": p[0], "cy": p[1], "scale": p[2], "sag": p[5],
                    "rotation": rotation(p[3], p[4]).tolist(), "distance_radii": distance}

        def predicted_lugs(p, pose):
            angles = p[7]-np.arange(len(lugs))*2*np.pi/len(lugs)
            return project(np.column_stack([p[6]*np.cos(angles), p[6]*np.sin(angles), np.full(len(lugs), -p[5])]), pose)

        def residual(p):
            pose = unpack(p)
            return np.r_[ring_residual(rim[::2], pose),
                         (predicted_lugs(p, pose)-lugs).ravel(),
                         (project([[0, 0, -p[5]]], pose)[0]-hub)]

        fit = least_squares(lambda p: residual(p)/scale, initial, bounds=(lower, upper),
                            loss="soft_l1", f_scale=.008, max_nfev=250)
        pose = unpack(fit.x)
        ring_errors = abs(ring_residual(rim[1::2], pose))
        lug_errors = np.linalg.norm(predicted_lugs(fit.x, pose)-lugs, axis=1)
        anchor_rms = float(np.sqrt(np.mean(residual(fit.x)**2)))
        selection_score = float(np.sqrt(np.mean(ring_errors**2))+anchor_rms)
        candidates.append({**pose, "converged": bool(fit.success), "anchor_rms_px": anchor_rms,
                           "ring_held_out_median_px": float(np.median(ring_errors)),
                           "ring_check_rms_px": float(np.sqrt(np.mean(ring_errors**2))),
                           "lug_median_px": float(np.median(lug_errors)),
                           "lug_radius": float(fit.x[6]), "selection_score_px": selection_score})
    valid = [c for c in candidates if c["converged"]]
    if not valid:
        raise ValueError("相机候选未收敛；请检查结构标注。")
    # Within 0.1% of image radius, prefer the weaker-perspective hypothesis.
    best = min(c["selection_score_px"] for c in valid)
    chosen = max((c for c in valid if c["selection_score_px"] <= best+.001*scale), key=lambda c: c["distance_radii"])
    return {**chosen, "model": "rim-lug-anchor-v2", "depth_measured": False,
            "selection_basis": "rim_even + all_lugs + hub fit; rim_odd selection; no spoke observations",
            "candidates": candidates}
