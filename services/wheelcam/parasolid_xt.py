"""Key dimensions of a wheel from a Parasolid text transmit file (.x_t), without a Parasolid kernel.

Open-source CAD kernels do not read Parasolid, and the factory CAD of the real orders is x_t
(NX / Unigraphics, schema 30 and 38). The analytic geometry is written as plain records, so the
records WheelCAM needs are read directly and each one is checked by its own geometry (unit axes,
orthogonal reference directions, sin^2 + cos^2 = 1), which rejects the odd false match:

    29 POINT     node attr owner next prev                          x y z
    31 CIRCLE    node attr owner next prev geom                     centre normal x_axis radius
    50 PLANE     node attr owner next prev geom sense               point normal x_axis
    51 CYLINDER  node attr owner next prev geom sense               point axis radius x_axis
    52 CONE      node attr owner next prev geom sense               point axis radius sin cos x_axis

(B-spline spoke faces are not read.) Lengths are metres in the file and millimetres here.
`wheel_truth` turns them into the checks of an order: width between the flange inner faces, ET
from the mounting face to the centre plane, bolt holes and seats, centre bore, spoke symmetry.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

NUM = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)"
INT = r"(\d+)"


def _body(text: str) -> str:
    """The data section as one line: the file wraps at 80 characters, inside tokens too."""
    if "**END_OF_HEADER" not in text:
        raise ValueError("not a Parasolid text transmit file")
    data = text.split("**END_OF_HEADER", 1)[1].split("\n", 1)[1].replace("\r", "")
    first, _, rest = data.partition("\n")
    if "TRANSMIT FILE" in first:                   # its own line, not part of the wrapped data
        data = rest
    return data.replace("\n", "")


def _records(body, code, ints, sense, floats):
    pat = rf"(?<![\d.e+-]){code} " + " ".join([INT] * ints) + (" ([+-])" if sense else " ") + " ".join([NUM] * floats)
    if sense:
        pat = pat.replace(" ([+-]) ", " ([+-])")        # the sense character is glued to the next number
    rows = [[float(v) for v in m.groups()[ints + sense:]] for m in re.finditer(pat, body)]
    return np.array(rows).reshape(-1, floats)


def _unit(v):
    return np.abs(np.linalg.norm(v, axis=1) - 1) < 1e-6


def read_xt(text: str) -> dict:
    """Checked analytic records, lengths in mm: points (N,3), circles (centre, normal, radius),
    planes (point, normal), cylinders (point, axis, radius), cones (point, axis, radius, half angle deg)."""
    body = _body(text)
    pts = _records(body, 29, 6, 0, 3) * 1000
    c = _records(body, 31, 7, 0, 10)
    c = c[_unit(c[:, 3:6]) & _unit(c[:, 6:9]) & (np.abs(np.sum(c[:, 3:6] * c[:, 6:9], axis=1)) < 1e-6)]
    pl = _records(body, 50, 7, 1, 9)
    pl = pl[_unit(pl[:, 3:6]) & _unit(pl[:, 6:9])]
    cy = _records(body, 51, 7, 1, 10)
    cy = cy[_unit(cy[:, 3:6]) & _unit(cy[:, 7:10]) & (np.abs(np.sum(cy[:, 3:6] * cy[:, 7:10], axis=1)) < 1e-6)]
    co = _records(body, 52, 7, 1, 12)
    co = co[_unit(co[:, 3:6]) & (np.abs(co[:, 7] ** 2 + co[:, 8] ** 2 - 1) < 1e-6)]
    return {"points": pts,
            "circles": np.column_stack([c[:, 0:3] * 1000, c[:, 3:6], c[:, 9] * 1000]),
            "planes": np.column_stack([pl[:, 0:3] * 1000, pl[:, 3:6]]),
            "cylinders": np.column_stack([cy[:, 0:3] * 1000, cy[:, 3:6], cy[:, 6] * 1000]),
            "cones": np.column_stack([co[:, 0:3] * 1000, co[:, 3:6], co[:, 6] * 1000, np.degrees(np.arcsin(co[:, 7]))])}


def _axis(g) -> np.ndarray:
    """The wheel axis: the direction most cylinders share."""
    dirs = Counter(tuple(np.round(np.abs(a), 3)) for a in g["cylinders"][:, 3:6])
    a = np.array(dirs.most_common(1)[0][0], float)
    return a / np.linalg.norm(a)


def symmetry_order(angles, max_order=24, floor=.08) -> int:
    """Rotational order of points by their angles: the smallest k whose harmonic stands out."""
    mags = [abs(np.exp(1j * k * np.asarray(angles)).mean()) for k in range(1, max_order + 1)]
    best = max(mags[1:])
    for k in range(2, max_order + 1):
        if mags[k - 1] > max(floor, .5 * best):
            return k
    return 0


def _bolt_holes(g, k, o, lip_r) -> dict:
    """Bolt holes: cylinders along the axis, same radius and bolt circle, evenly spaced round it;
    of several such rings (decorative holes, cap screws) the one with conical or spherical seats."""
    cyl, cones = g["cylinders"], g["cones"]
    along = np.abs(np.abs(cyl[:, 3 + k]) - 1) < 1e-6
    xy = cyl[:, o]
    dist = np.hypot(xy[:, 0], xy[:, 1])
    pick = along & (cyl[:, 6] > 5.5) & (cyl[:, 6] < 12) & (dist > 35) & (dist < .4 * lip_r)
    groups = {}
    for (x, y), d, r in zip(xy[pick], dist[pick], cyl[pick, 6]):
        key = (round(float(d) * 2) / 2, round(float(r), 2))        # centres agree to ~0.1 mm
        pts = groups.setdefault(key, [])
        if not any(math.hypot(x - px, y - py) < .5 for px, py in pts):
            pts.append((x, y))
    seat_xy = cones[np.abs(np.abs(cones[:, 3 + k]) - 1) < 1e-6][:, o]
    best = None
    for (d, r), pts in groups.items():
        n = len(pts)
        if not 3 <= n <= 12:
            continue
        ang = np.sort(np.degrees(np.arctan2([p[1] for p in pts], [p[0] for p in pts])) % 360)
        gaps = np.diff(np.append(ang, ang[0] + 360))
        if np.abs(gaps - 360 / n).max() > 1.0:
            continue
        seated = sum(np.any(np.hypot(*(seat_xy - np.array(p)).T) < .5) for p in pts) == n
        score = (seated, -d)
        if best is None or score > best[0]:
            best = (score, d, r, n, pts)
    if best is None:
        return {}
    _, d, r, n, pts = best
    d = float(np.median([math.hypot(*p) for p in pts]))
    out = {"bolts": n, "pcd_mm": round(2 * d, 2), "bolt_hole_d_mm": round(2 * r, 2)}
    at = [c for c in cones if np.abs(np.abs(c[3 + k]) - 1) < 1e-6 and min(math.hypot(*(c[o] - np.array(p))) for p in pts) < .5]
    if at:                          # the seat is the widest cone there; a small one is the back chamfer
        seat = max(at, key=lambda c: c[6])
        out["seat_cone_deg"] = round(2 * float(seat[7]), 1)
    return out


def wheel_truth(g: dict) -> dict:
    """Order-level dimensions of the wheel in `g` (read_xt). Assumes the axis is a coordinate axis
    (true of the factory models: Z)."""
    ax = _axis(g)
    k = int(np.argmax(ax))
    if not math.isclose(ax[k], 1.0, abs_tol=1e-6):
        raise ValueError(f"wheel axis is not a coordinate axis: {ax}")
    o = [i for i in range(3) if i != k]
    radial = lambda p: np.hypot(p[:, o[0]], p[:, o[1]])
    circles = g["circles"]
    coax = circles[(radial(circles[:, 0:3]) < .01) & (np.abs(np.abs(circles[:, 3 + k]) - 1) < 1e-6)]
    profile = np.unique(np.round(np.column_stack([coax[:, 6], coax[:, k]]), 3), axis=0)
    z_lo, z_hi = profile[:, 1].min(), profile[:, 1].max()
    lip_r = profile[:, 0].max()
    planes = g["planes"]
    zs = np.unique(np.round(planes[np.abs(np.abs(planes[:, 3 + k]) - 1) < 1e-6][:, k], 3))
    ring_at = lambda z, r0, r1: np.any((np.abs(coax[:, k] - z) < .01) & (coax[:, 6] > r0) & (coax[:, 6] < r1))
    # Flange inner faces: the flat faces at the flange radius nearest the middle, one each side
    # (the outer faces of the flanges lie beyond them). Their spacing is the J width.
    mid = (z_lo + z_hi) / 2
    flange = [z for z in zs if ring_at(z, lip_r - 30, lip_r + 1)]
    a = max((z for z in flange if z < mid), default=z_lo)
    b = min((z for z in flange if z > mid), default=z_hi)
    # A flange whose inner face is not flat leaves only its outer face: the spacing is then no whole
    # half inch and the width and ET are not read (the rear flange of one factory model).
    width_ok = abs((b - a) / 12.7 - round((b - a) / 12.7)) < .02
    centre = (a + b) / 2
    # Mounting face: the lowest flat face with coaxial edges in the hub (the spoke side is toward
    # +axis in the factory models); the bolt holes are the circles on it off the axis, and the centre
    # bore the narrowest coaxial edge within 15 mm above it (a cap recess further up is narrower).
    hub = [z for z in zs if ring_at(z, 15, .45 * lip_r)]
    mount = float(hub[0]) if hub else None
    bolt, bore_d = {}, None
    if mount is not None:
        bolt = _bolt_holes(g, k, o, lip_r)
        above = coax[(coax[:, k] >= mount - .01) & (coax[:, k] <= mount + 15) & (coax[:, 6] > 15)]
        if len(above):
            bore_d = round(2 * float(above[:, 6].min()), 2)
    spokes_zone = g["points"][(radial(g["points"]) > .35 * lip_r) & (radial(g["points"]) < .8 * lip_r)]
    order = symmetry_order(np.arctan2(spokes_zone[:, o[1]], spokes_zone[:, o[0]])) if len(spokes_zone) else 0
    out = {"axis": "xyz"[k], "overall_width_mm": round(z_hi - z_lo, 2), "lip_od_mm": round(2 * lip_r, 2),
           "flange_inner_faces_mm": [round(a, 2), round(b, 2)] if width_ok else None,
           "width_in": round((b - a) / 25.4, 3) if width_ok else None,
           "centre_plane_mm": round(centre, 2) if width_ok else None, "center_bore_mm": bore_d,
           **bolt, "rotational_order": order, "profile_rz": profile.tolist()}
    if mount is not None:
        out["mount_face_mm"] = round(mount, 2)
        out["et_mm"] = round(mount - centre, 2) if width_ok else None
    return out
