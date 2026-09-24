"""Photo-constrained master surface, normalized to rim radius = 1.

This is a visual design mesh, not a CAD solid. Validation observations never
participate in camera or surface construction. No window-count assumption.
"""
from collections import Counter
from functools import lru_cache
import json
import math
import struct
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.interpolate import CubicSpline
from scipy.optimize import brentq, least_squares
from scipy.spatial import Delaunay
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .windows import _inside, _cross_pairs, _point_segment, self_intersects
from .sector_camera import fit_candidates, project
from .sector_fairing import attachment_edges, fair_relief
from .sector_patches import boundary_patch_relief, section_cage_relief

VERSION = "master-surface-v10"
FULL_WHEEL_VERSION = "sixfold-preview-v1"


class SurfaceControls(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    crown: float = Field(default=.008, ge=0, le=.025)
    ridge: float = Field(default=.008, ge=0, le=.025)
    groove: float = Field(default=.004, ge=0, le=.012)
    edge_width_px: float = Field(default=2.5, ge=1, le=8)
    ridge_width_px: float = Field(default=2.5, ge=1, le=8)
    thickness: float = Field(default=.035, ge=.015, le=.08)
    camera_model: Literal["legacy", "anchors"] = "legacy"
    relief_profile: Literal["legacy", "smooth"] = "smooth"
    end_blend_px: float = Field(default=12, ge=2, le=30)
    junction_mode: Literal["capped", "continuous"] = "continuous"
    fairing_px: float = Field(default=1, ge=0, le=3)
    surface_scope: Literal["spoke", "root-panel"] = "root-panel"
    opening_mode: Literal["through", "recess"] = "through"
    pocket_depth: float = Field(default=.010, ge=.002, le=.012)
    hole_shoulder_depth: float = Field(default=.004, ge=0, le=.008)
    hole_shoulder_width_px: float = Field(default=2, ge=1, le=4)
    root_sampling: Literal["fine", "legacy"] = "fine"
    surface_model: Literal["heightfield-v7", "section-cage-v8", "boundary-patches-v10"] = "heightfield-v7"


def fit_camera(data):
    points = np.asarray(data["rim_points"], float)
    train, held = points[::2], points[1::2]

    center = np.median(points, axis=0)
    image_xy = np.column_stack([points[:, 0]-center[0], center[1]-points[:, 1]])
    values, vectors = np.linalg.eigh(np.cov(image_xy.T))
    major = vectors[:, int(np.argmax(values))]
    axes = np.sqrt(np.maximum(2*values, 1e-8))
    initial_a, initial_b = float(axes.max()), float(axes.min())
    scale = max(initial_a, float(np.ptp(points, axis=0).max()/2), 1.0)
    # In the residual frame the major axis is local Y = [-sin(phi), cos(phi)].
    initial_phi = math.atan2(-major[0], major[1])

    def residual(p, pts):
        cx, cy, a, ratio, phi = p
        b = a*ratio
        u, v = pts[:, 0] - cx, cy - pts[:, 1]
        x, y = np.cos(phi)*u + np.sin(phi)*v, -np.sin(phi)*u + np.cos(phi)*v
        return (np.sqrt((x/b)**2 + (y/a)**2) - 1)*b

    initial = [*center, initial_a, np.clip(initial_b/initial_a, .3, 1), initial_phi]
    lower = [center[0]-scale*.3, center[1]-scale*.3, scale*.55, .25, initial_phi-math.pi/2]
    upper = [center[0]+scale*.3, center[1]+scale*.3, scale*1.45, 1., initial_phi+math.pi/2]
    fit = least_squares(lambda p: residual(p, train), initial, bounds=(lower, upper), loss="soft_l1")
    cx, cy, a, ratio, phi = fit.x
    theta = np.arccos(np.clip(ratio, 0, 1))
    r0 = np.array([np.cos(theta)*np.cos(phi), -np.sin(phi), np.sin(theta)*np.cos(phi)])
    r1 = np.array([np.cos(theta)*np.sin(phi), np.cos(phi), np.sin(theta)*np.sin(phi)])
    rotation = np.stack([r0, r1, np.cross(r0, r1)])
    hub = np.array([data["hub_center"][0]-cx, cy-data["hub_center"][1]])/a
    denominator = float(np.dot(rotation[:2, 2], rotation[:2, 2]))
    sag = 0.0 if denominator < 4e-4 else float(np.clip(-np.dot(hub, rotation[:2, 2])/denominator, -.9, .9))
    return {"cx": float(cx), "cy": float(cy), "scale": float(a), "sag": sag,
            "rotation": rotation.tolist(), "ring_held_out_median_px": float(np.median(abs(residual(fit.x, held)))),
            "fit_is_image_normalized": True}


def lift(points, pose):
    rotation = np.asarray(pose["rotation"])
    distance = pose.get("distance_radii", 1e6)
    inverse_distance = 0 if distance >= 1e5 else 1/distance
    result = []
    for u, v in points:
        q = np.array([(u-pose["cx"])/pose["scale"], (pose["cy"]-v)/pose["scale"]])
        inverse = np.linalg.inv(rotation[:2, :2]+np.outer(q, rotation[2, :2])*inverse_distance)

        def xy(z):
            return inverse @ (q-(rotation[:2, 2]+q*rotation[2, 2]*inverse_distance)*z)

        def residual(z):
            t = np.clip((np.linalg.norm(xy(z))-.16)/.72, 0, 1)
            return z + pose["sag"]*(1-(3*t*t-2*t*t*t))

        z = brentq(residual, -pose["sag"]-1e-8, 1e-8)
        result.append([*xy(z), z])
    return np.asarray(result)


def boundary_curve(points, spacing=4):
    p = np.array([*points, points[0]], float)
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    fn = CubicSpline(s, p, bc_type="periodic")
    result = fn(np.unique(np.r_[np.arange(0, s[-1], spacing), s[:-1]]))
    if self_intersects(result):
        raise ValueError("母辐条边界自相交，请修正标注。")
    return result


def cross(a, b):
    return a[..., 0]*b[..., 1] - a[..., 1]*b[..., 0]


def triangulate(boundary):
    """Ear clipping keeps the concave Y opening; no convex hull shortcut."""
    p = np.asarray(boundary)
    ids = list(range(len(p)))
    if np.sum(cross(p, np.roll(p, -1, axis=0))) < 0:
        ids.reverse()
    faces = []
    while len(ids) > 3:
        for j in range(len(ids)):
            a, b, c = ids[j-1], ids[j], ids[(j+1) % len(ids)]
            if cross(p[b]-p[a], p[c]-p[b]) <= 1e-9:
                continue
            others = p[[i for i in ids if i not in (a, b, c)]]
            inside = ((cross(p[b]-p[a], others-p[a]) >= -1e-9)
                      & (cross(p[c]-p[b], others-p[b]) >= -1e-9)
                      & (cross(p[a]-p[c], others-p[c]) >= -1e-9))
            if not inside.any():
                faces.append([a, b, c])
                ids.pop(j)
                break
        else:
            raise ValueError("母辐条边界无法三角化。")
    return np.array([*faces, ids], int)


def refine(points, faces, levels=3):
    vertices = np.asarray(points).tolist()
    for _ in range(levels):
        midpoint, new_faces = {}, []

        def split(a, b):
            key = tuple(sorted((a, b)))
            if key not in midpoint:
                midpoint[key] = len(vertices)
                vertices.append(((np.array(vertices[a])+vertices[b])/2).tolist())
            return midpoint[key]

        for a, b, c in faces:
            ab, bc, ca = split(a, b), split(b, c), split(c, a)
            new_faces.extend([[a, ab, ca], [ab, b, bc], [ca, bc, c], [ab, bc, ca]])
        faces = new_faces
    return np.asarray(vertices), np.asarray(faces, int)


def surface_triangulation(boundary, spacing=1.5):
    loops, points, faces = perforated_triangulation(boundary, [], spacing)
    return loops[0], points, faces


def validate_openings(boundary, holes):
    for loop in [boundary, *holes]:
        if (len(loop) < 3 or not np.isfinite(loop).all()
                or abs(np.sum(cross(loop, np.roll(loop, -1, axis=0)))) < 1e-8
                or np.min(np.linalg.norm(np.roll(loop, -1, axis=0)-loop, axis=1)) < 1e-8):
            raise ValueError("轮廓必须有非零面积，且不能有重复相邻点。")
    for index, hole in enumerate(holes):
        if self_intersects(hole) or not _inside(hole, boundary).all():
            raise ValueError("孔轮廓必须完整位于外轮廓内，且不能自相交。")
        for other in [boundary, *holes[:index]]:
            if (_cross_pairs(hole, np.roll(hole, -1, axis=0), other, np.roll(other, -1, axis=0)).any()
                    or min(_point_segment(hole, other, np.roll(other, -1, axis=0)).min(),
                           _point_segment(other, hole, np.roll(hole, -1, axis=0)).min()) < .5):
                raise ValueError("孔边与外缘或另一孔过近、接触或相交，请保留薄肋。")
        if any(_inside(hole[:1], other).any() or _inside(other[:1], hole).any() for other in holes[:index]):
            raise ValueError("孔轮廓不能互相嵌套。")


def perforated_triangulation(boundary, holes, spacing=1.5):
    """Uniform interior samples with recovered polygon constraints.

    Long ear-clipping fans under-sample narrow relief bands even after subdivision.
    Recover missing boundary edges by splitting them BEFORE selecting interior
    triangles. This retains concavities and prevents holes from centroid clipping.
    """
    boundary, holes = np.asarray(boundary, float), [np.asarray(h, float) for h in holes]
    validate_openings(boundary, holes)
    loops = []
    for loop in [boundary, *holes]:
        dense = []
        for a, b in zip(loop, np.roll(loop, -1, axis=0)):
            count = max(1, math.ceil(np.linalg.norm(b-a)/spacing))
            dense.extend(a+(b-a)*t/count for t in range(count))
        loops.append(np.array(dense))
    boundary = loops[0]
    def inside(points):
        result = _inside(points, loops[0])
        for loop in loops[1:]:
            result &= ~_inside(points, loop)
        return result
    low, high = boundary.min(0), boundary.max(0)
    x, y = np.meshgrid(np.arange(low[0], high[0], spacing), np.arange(low[1], high[1], spacing))
    grid = np.column_stack([x.ravel(), y.ravel()])
    grid = grid[inside(grid)]
    grid = grid[distance_to_lines(grid, loops, closed=True) > spacing*.25]
    for _ in range(12):
        points = np.vstack([*loops, grid])
        faces = Delaunay(points).simplices
        # Qhull can emit zero-area triangles on collinear recovered boundary
        # segments. They are not surface area and must not become false walls.
        twice_area = abs(cross(points[faces[:, 1]]-points[faces[:, 0]], points[faces[:, 2]]-points[faces[:, 0]]))
        faces = faces[twice_area > 1e-10]
        edges = {tuple(sorted((int(a), int(b)))) for f in faces for a, b in zip(f, np.roll(f, -1))}
        offsets = np.cumsum([0]+[len(loop) for loop in loops])
        expected = {tuple(sorted((int(offset+i), int(offset+(i+1) % len(loop)))))
                    for offset, loop in zip(offsets, loops) for i in range(len(loop))}
        missing = expected-edges
        if not missing:
            faces = faces[inside(points[faces].mean(1))]
            areas = [abs(np.sum(cross(loop, np.roll(loop, -1, axis=0))))/2 for loop in loops]
            polygon_area = areas[0]-sum(areas[1:])
            triangle_area = abs(cross(points[faces[:, 1]]-points[faces[:, 0]], points[faces[:, 2]]-points[faces[:, 0]])).sum()/2
            if not np.isclose(polygon_area, triangle_area, rtol=1e-8):
                raise ValueError("曲面网格未完整覆盖母辐条，请检查边界。")
            counts = Counter(tuple(sorted((int(a), int(b)))) for f in faces for a, b in zip(f, np.roll(f, -1)))
            actual = {edge for edge, count in counts.items() if count == 1}
            if actual != expected:
                raise ValueError("曲面外缘与标注边界不一致。")
            return loops, points, faces
        new_loops = []
        for offset, loop in zip(offsets, loops):
            dense = []
            for i, a in enumerate(loop):
                dense.append(a)
                if tuple(sorted((int(offset+i), int(offset+(i+1) % len(loop))))) in missing:
                    dense.append((a+loop[(i+1) % len(loop)])/2)
            new_loops.append(np.array(dense))
        loops = new_loops
    raise ValueError("曲面边界约束无法恢复，请检查过近或重合的边界点。")


def distance_to_lines(points, guides, closed=False):
    values = []
    for guide in guides:
        p = np.asarray(guide, float)
        if closed:
            p = np.vstack([p, p[0]])
        a, delta = p[:-1], np.diff(p, axis=0)
        t = np.clip(((points[:, None]-a)*delta).sum(-1)/np.maximum((delta*delta).sum(-1), 1e-12), 0, 1)
        values.append(np.linalg.norm(points[:, None]-a-t[..., None]*delta, axis=-1).min(axis=1))
    return np.min(values, axis=0) if values else np.full(len(points), np.inf)


def smooth_guide(guide, spacing=.75):
    """Arc-length parameterized C2 guide, retaining its annotation knots."""
    p = np.asarray(guide, float)
    p = p[np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-8]]
    if len(p) < 2:
        raise ValueError("脊槽导线至少需要两个不同的点。")
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    knots = np.unique(np.r_[np.arange(0, s[-1], spacing), s])
    return CubicSpline(s, p, bc_type="natural")(knots)


def guide_field(points, guides, width, blend):
    """Smooth union avoids the bisector crease from nearest-of-two ridges.

    End taper follows guide arc length, so root/tip details merge gradually
    into the crown instead of terminating in round Gaussian bumps.
    """
    remaining = np.ones(len(points))
    for guide in guides:
        p = smooth_guide(guide)
        delta = np.diff(p, axis=0)
        lengths = np.linalg.norm(delta, axis=1)
        t = np.clip(((points[:, None]-p[:-1])*delta).sum(-1)/np.maximum(lengths**2, 1e-12), 0, 1)
        distances = np.linalg.norm(points[:, None]-p[:-1]-t[..., None]*delta, axis=-1)
        nearest = distances.argmin(axis=1)
        rows = np.arange(len(points))
        along = np.r_[0, np.cumsum(lengths)][nearest]+t[rows, nearest]*lengths[nearest]
        end = np.clip(np.minimum(along, lengths.sum()-along)/blend, 0, 1)
        taper = end**3*(10-15*end+6*end**2)
        remaining *= 1-np.exp(-.5*(distances[rows, nearest]/width)**2)*taper
    return 1-remaining


def relief(points, boundary, evidence, controls):
    if controls.junction_mode == "continuous":
        seams = [evidence["master"]["root_seam"], *evidence["master"]["tip_seams"]]
        cuts = attachment_edges(boundary, seams)
        edge = distance_to_lines(points, [[a, b] for a, b, cut in zip(boundary, np.roll(boundary, -1, axis=0), cuts) if not cut])
    else:
        edge = distance_to_lines(points, [boundary], closed=True)
    t = np.clip(edge/controls.edge_width_px, 0, 1)
    envelope = t*t*(3-2*t)
    if controls.relief_profile == "smooth":
        envelope = t**3*(10-15*t+6*t*t)
        if controls.surface_model in {"section-cage-v8", "boundary-patches-v10"}:
            if controls.surface_model == "boundary-patches-v10":
                cage, coverage, report = boundary_patch_relief(
                    points, boundary, evidence["master"], controls.crown, controls.ridge, controls.groove)
            else:
                cage, coverage, report = section_cage_relief(
                    points, evidence["master"], controls.crown, controls.ridge, controls.groove)
            # The root panel is not yet a dedicated patch.  Preserve a subdued
            # crown there rather than extending an arm section across the holes.
            root = controls.crown*.45
            return envelope*(coverage*cage+(1-coverage)*root), report
        ridge = guide_field(points, evidence["master"].get("ridge_guides", []), controls.ridge_width_px, controls.end_blend_px)
        groove = guide_field(points, evidence["master"].get("groove_guides", []), controls.ridge_width_px, controls.end_blend_px)
        return envelope*(controls.crown+controls.ridge*ridge-controls.groove*groove), None
    ridge = distance_to_lines(points, evidence["master"].get("ridge_guides", []))
    groove = distance_to_lines(points, evidence["master"].get("groove_guides", []))
    return (envelope*(controls.crown + controls.ridge*np.exp(-.5*(ridge/controls.ridge_width_px)**2)
                     - controls.groove*np.exp(-.5*(groove/controls.ridge_width_px)**2)), None)


def score_boundary(xyz, pose, observations):
    angle = math.radians(observations["group_rotation_deg"])
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    predicted = project(xyz @ rotation.T, pose)
    errors = distance_to_lines(np.asarray(observations["points"]), [predicted], closed=True)
    return {"median_px": float(np.median(errors)), "max_px": float(errors.max()),
            "p95_px": float(np.percentile(errors, 95)), "per_point_px": errors.tolist(),
            "predicted": predicted.tolist(), "observed": observations["points"],
            "threshold_passed": bool(np.median(errors) <= 5 and errors.max() <= 12)}


@lru_cache(maxsize=8)
def surface_basis(serialized_evidence, camera_model="legacy", surface_scope="spoke", opening_mode="through", root_sampling="fine"):
    evidence = json.loads(serialized_evidence)
    pose = fit_candidates(evidence) if camera_model == "anchors" else {**fit_camera(evidence), "model": "rim-only-v1", "distance_radii": 1e6, "depth_measured": False}
    key = "panel_boundary" if surface_scope == "root-panel" else "boundary"
    boundary = boundary_curve(evidence["master"][key])
    holes = [boundary_curve(p, spacing=.75) for p in evidence["uncertain_pockets"]] if surface_scope == "root-panel" else []
    validate_openings(boundary, holes)
    spacing = .75 if surface_scope == "root-panel" and root_sampling == "fine" else 1.5
    loops, uv, faces = perforated_triangulation(boundary, holes if opening_mode == "through" else [], spacing)
    base = lift(uv, pose)
    return pose, loops, uv, faces, base, holes


def hole_shoulder(points, boundary, holes, depth, width):
    """Assumed axial lip rolloff, not a measured fillet or a wall bevel.

    Preserve XY contours and outer-edge heights. Fade before reaching unrelated
    parts of the spoke; reduce the drop where a narrow bridge meets the exterior.
    A zero depth exactly disables this treatment for legacy comparison.
    """
    inner = np.clip(distance_to_lines(points, holes, closed=True)/width, 0, 1)
    outer = np.clip(distance_to_lines(points, [boundary], closed=True)/width, 0, 1)
    smooth = lambda t: t**3*(10-15*t+6*t*t)
    return -depth*(1-smooth(inner))*smooth(outer)


def opening_shape_metrics(loop):
    """Image-plane descriptors for review, not inferred physical dimensions.

    Principal extents work for slots at any angle and do not assume a fixed
    wheel orientation. They make an accidentally short or stocky trace visible
    without forcing every opening into a capsule primitive.
    """
    points = np.asarray(loop, float)
    centered = points-points.mean(axis=0)
    _, _, axes = np.linalg.svd(centered, full_matrices=False)
    coordinates = centered @ axes.T
    extents = np.ptp(coordinates, axis=0)
    major, minor = sorted((float(extents[0]), float(extents[1])), reverse=True)
    direction = axes[0] if extents[0] >= extents[1] else axes[1]
    if direction[0] < 0:
        direction = -direction
    return {"major_extent_px": major, "minor_extent_px": minor,
            "aspect_ratio": major/max(minor, 1e-12),
            "axis_image": direction.tolist(), "measured_in_image": True}


def loop_clearance(first, second):
    """Minimum image-plane gap between two already validated closed loops."""
    a, b = np.asarray(first, float), np.asarray(second, float)
    return float(min(_point_segment(a, b, np.roll(b, -1, axis=0)).min(),
                     _point_segment(b, a, np.roll(a, -1, axis=0)).min()))


def opening_rib_metrics(boundary, holes):
    """Report visible material left around each opening in source pixels.

    This is a review measurement only. A photograph cannot establish physical
    wall thickness, and the value must not be converted to millimetres.
    """
    result = []
    for index, hole in enumerate(holes):
        outer = loop_clearance(hole, boundary)
        neighbours = [loop_clearance(hole, other) for j, other in enumerate(holes) if j != index]
        nearest = min(neighbours) if neighbours else None
        result.append({"to_outer_px": outer, "to_other_opening_px": nearest,
                       "minimum_visible_rib_px": min([outer, *neighbours]),
                       "measured_in_image": True})
    return result


def build_surface(evidence, controls, holdout=None):
    pose, loops, uv, faces, base, holes = surface_basis(json.dumps(evidence, sort_keys=True), controls.camera_model, controls.surface_scope, controls.opening_mode, controls.root_sampling)
    boundary = loops[0]
    top = base.copy()
    # Real axial displacement: do NOT ray-lift again to hide the projection change.
    target, section_cage = relief(uv, boundary, evidence, controls)
    if holes:
        active_holes = loops[1:] if controls.opening_mode == "through" else holes
        distance = distance_to_lines(uv, active_holes, closed=True)
        t = np.clip(distance/controls.edge_width_px, 0, 1)
        ramp = t**3*(10-15*t+6*t*t)
        if controls.opening_mode == "through":
            target *= ramp
            target += hole_shoulder(uv, boundary, active_holes, controls.hole_shoulder_depth, controls.hole_shoulder_width_px)
        else:
            inside = np.logical_or.reduce([_inside(uv, hole) for hole in holes])
            target -= controls.pocket_depth*ramp*inside
    displacement, fairing = fair_relief(uv, faces, target, sum(map(len, loops)), controls.fairing_px)
    top[:, 2] += displacement
    # Orient front triangles in world XY (source-image Y points down).
    if np.median(cross(top[faces[:, 1], :2]-top[faces[:, 0], :2], top[faces[:, 2], :2]-top[faces[:, 0], :2])) < 0:
        faces = faces[:, ::-1]
    counts = Counter(tuple(sorted((int(a), int(b)))) for f in faces for a, b in zip(f, np.roll(f, -1)))
    edges = [(int(a), int(b)) for f in faces for a, b in zip(f, np.roll(f, -1)) if counts[tuple(sorted((int(a), int(b))))] == 1]
    n = len(top)
    bottom = base.copy()
    bottom[:, 2] -= controls.thickness
    if np.min(top[:, 2]-bottom[:, 2]) <= 0:
        raise ValueError("曲面平顺后厚度非正，请降低凹槽深度或平顺宽度。")
    walls = [[b, a, a+n] for a, b in edges] + [[b, a+n, b+n] for a, b in edges]
    triangles = np.vstack([faces, faces[:, ::-1]+n, walls])
    vertices = np.vstack([top, bottom])
    edge_counts = Counter(tuple(sorted((int(a), int(b)))) for f in triangles for a, b in zip(f, np.roll(f, -1)))
    edge_array = np.array(list(edge_counts))
    graph = coo_matrix((np.ones(len(edge_array)), (edge_array[:, 0], edge_array[:, 1])), shape=(len(vertices), len(vertices)))
    components = connected_components(graph, directed=False, return_labels=False)
    euler = len(vertices)-len(edge_counts)+len(triangles)
    if components != 1 or any(count != 2 for count in edge_counts.values()) or euler != 2-2*(len(loops)-1):
        raise ValueError("局部孔区网格连接或封闭拓扑不符合要求。")
    boundary_xyz = top[:len(boundary)]
    diagnostic = score_boundary(boundary_xyz, pose, evidence["validation"])
    independent = score_boundary(boundary_xyz, pose, holdout) if holdout else None
    if independent:
        independent.update({"annotation_reviewed": holdout.get("reviewed", False),
                            "used_for_optimization": False, "used_for_model_comparison": True,
                            "scope": "固定人工观察点，未参与数值拟合；已用于版本比较，不是最终盲测，非工程精度"})
    passed = bool(controls.surface_scope == "spoke" and independent and independent["annotation_reviewed"] and independent["threshold_passed"] and diagnostic["threshold_passed"])
    algorithms = {"heightfield-v7": "master-surface-v7", "section-cage-v8": "master-surface-v8",
                  "boundary-patches-v10": "master-surface-v10"}
    return {"algorithm": algorithms[controls.surface_model],
            "controls": controls.model_dump(), "surface_model": controls.surface_model,
            "section_cage": section_cage, "units": "rim_radius=1; unmeasured",
            "groups": evidence["structure"]["spoke_groups"], "lug_count": len(evidence["structure"]["lug_centers"]),
            "camera": pose, "boundary": boundary.tolist(), "boundary_xyz": boundary_xyz.tolist(),
            "fairing": fairing, "junctions": {"mode": controls.junction_mode,
                "connected_to_hub_or_rim": False, "cut_caps_present": True,
                "note": ("连接切口正面不倒圆；底部与侧壁仍临时封口，未连接中心盘或轮辋。"
                         if controls.junction_mode == "continuous" else "旧版对照：连接切口也按外缘倒圆，仍为独立封闭局部网格。")},
            "openings": {"scope": controls.surface_scope, "mode": controls.opening_mode if holes else "none",
                "count": len(holes), "contours": [h.tolist() for h in holes],
                "shape_metrics": [opening_shape_metrics(h) for h in holes],
                "rib_metrics": opening_rib_metrics(boundary, holes),
                "inner_wall_triangles": 2*sum(map(len, loops[1:])), "depth_measured": False,
                "outer_and_hole_edges_reviewed": False,
                "shoulder": {"enabled": bool(holes and controls.opening_mode == "through" and controls.hole_shoulder_depth > 0),
                    "target_depth_R": controls.hole_shoulder_depth if holes and controls.opening_mode == "through" else 0,
                    "width_px": controls.hole_shoulder_width_px,
                    "note": "孔口轴向下沉过渡，平面轮廓不变；不是实测倒圆，侧壁与背面出口仍是假设。"},
                "note": "根部两侧薄肋与主辐条共享顶点；孔轮廓为人工解释，孔深和背面出口未测量。" if holes else "旧版单辐条，不含两侧孔区。"},
            "sampling": {"spacing_px": .75 if holes and controls.root_sampling == "fine" else 1.5,
                         "note": "增加采样只改善曲面表达，不增加照片证据。"},
            "positions": vertices.tolist(), "triangles": triangles.tolist(), "top_triangle_count": len(faces),
            "diagnostic": diagnostic, "holdout": independent,
            "integrity": {"nonmanifold_edges": sum(v != 2 for v in edge_counts.values()),
                          "connected_components": int(components), "euler_characteristic": int(euler),
                          "min_axial_thickness": float((top[:, 2]-bottom[:, 2]).min()),
                          "self_intersection_checked": False},
            "gate": {"passed": passed, "full_wheel_generated": False,
                     "reason": ("孔区外缘与内孔需新增独立复核；旧观察只评价外缘，不能验收孔结构。" if holes else
                                "需独立标注复核且两组误差均满足中位 ≤ 5 px、最大 ≤ 12 px；之后才接整轮。")},
            "engineering_approved": False}


def _display_arrays(result):
    """Split front/back/walls so their normals do not smear across seams."""
    source_points = np.array(result["positions"], dtype="<f4")
    source_faces = np.array(result["triangles"], dtype="<u4")
    top_count = result["top_triangle_count"]
    point_parts, face_parts, offset = [], [], 0
    for group in (source_faces[:top_count], source_faces[top_count:2*top_count], source_faces[2*top_count:]):
        used, inverse = np.unique(group, return_inverse=True)
        point_parts.append(source_points[used])
        face_parts.append(inverse.reshape(-1, 3)+offset)
        offset += len(used)
    points = np.concatenate(point_parts)[:, [0, 2, 1]]
    points[:, 2] *= -1
    faces = np.concatenate(face_parts).astype("<u4")
    return points, faces


def _normals(points, faces):
    normals = np.zeros_like(points, dtype="<f4")
    face_normals = np.cross(points[faces[:, 1]]-points[faces[:, 0]], points[faces[:, 2]]-points[faces[:, 0]])
    for index in range(3):
        np.add.at(normals, faces[:, index], face_normals)
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-12)
    return normals.astype("<f4")


def _annular_prism(inner, outer, front, back, segments=128):
    """Visual context ring in source Z-up coordinates, converted to glTF Y-up."""
    angles = np.linspace(0, 2*math.pi, segments, endpoint=False)
    rings = []
    for z, radius in ((front, outer), (front, inner), (back, outer), (back, inner)):
        rings.append(np.column_stack([radius*np.cos(angles), radius*np.sin(angles), np.full(segments, z)]))
    source = np.vstack(rings)
    faces = []
    for index in range(segments):
        nxt = (index+1) % segments
        for a, b, c, d in ((index, nxt, segments+nxt, segments+index),
                           (2*segments+index, 3*segments+index, 3*segments+nxt, 2*segments+nxt),
                           (index, 2*segments+index, 2*segments+nxt, nxt),
                           (segments+index, segments+nxt, 3*segments+nxt, 3*segments+index)):
            faces.extend(((a, b, c), (a, c, d)))
    points = source[:, [0, 2, 1]].astype("<f4")
    points[:, 2] *= -1
    return points, np.asarray(faces, dtype="<u4")


def _solid_cylinder(radius, front, back, segments=48):
    angles = np.linspace(0, 2*math.pi, segments, endpoint=False)
    source = np.vstack([
        np.column_stack([radius*np.cos(angles), radius*np.sin(angles), np.full(segments, front)]),
        np.column_stack([radius*np.cos(angles), radius*np.sin(angles), np.full(segments, back)]),
        [[0, 0, front], [0, 0, back]],
    ])
    front_center, back_center = 2*segments, 2*segments+1
    faces = []
    for index in range(segments):
        nxt = (index+1) % segments
        faces.extend(((front_center, index, nxt), (back_center, segments+nxt, segments+index),
                      (index, segments+index, segments+nxt), (index, segments+nxt, nxt)))
    points = source[:, [0, 2, 1]].astype("<f4")
    points[:, 2] *= -1
    return points, np.asarray(faces, dtype="<u4")


def _merge_meshes(meshes):
    points, faces, offset = [], [], 0
    for mesh_points, mesh_faces in meshes:
        points.append(mesh_points)
        faces.append(mesh_faces+offset)
        offset += len(mesh_points)
    return np.vstack(points).astype("<f4"), np.vstack(faces).astype("<u4")


def _encode_glb(meshes, nodes, scene_nodes, extras, generator):
    raw = bytearray()
    views, accessors, primitives = [], [], []
    for points, faces, material in meshes:
        normals = _normals(points, faces)
        attributes = []
        for array, component, kind in ((points.astype("<f4"), 5126, "VEC3"),
                                       (normals, 5126, "VEC3"),
                                       (faces.astype("<u4").reshape(-1), 5125, "SCALAR")):
            while len(raw) % 4:
                raw.append(0)
            offset = len(raw)
            raw.extend(array.tobytes())
            views.append({"buffer": 0, "byteOffset": offset, "byteLength": array.nbytes})
            accessor = {"bufferView": len(views)-1, "componentType": component,
                        "count": len(array), "type": kind}
            if kind == "VEC3" and len(attributes) == 0:
                accessor.update({"min": points.min(0).tolist(), "max": points.max(0).tolist()})
            accessors.append(accessor)
            attributes.append(len(accessors)-1)
        primitives.append({"attributes": {"POSITION": attributes[0], "NORMAL": attributes[1]},
                           "indices": attributes[2], "material": material})
    document = {
        "asset": {"version": "2.0", "generator": generator}, "scene": 0,
        "scenes": [{"nodes": scene_nodes}], "nodes": nodes,
        "meshes": [{"primitives": [primitive]} for primitive in primitives],
        "materials": [
            {"name": "neutral forged surface", "doubleSided": True,
             "pbrMetallicRoughness": {"baseColorFactor": [.48, .5, .53, 1], "metallicFactor": 0, "roughnessFactor": .75}},
            {"name": "assumed wheel context", "doubleSided": True,
             "pbrMetallicRoughness": {"baseColorFactor": [.34, .36, .39, 1], "metallicFactor": 0, "roughnessFactor": .8}},
            {"name": "display-only lug recess", "doubleSided": True,
             "pbrMetallicRoughness": {"baseColorFactor": [.055, .065, .075, 1], "metallicFactor": 0, "roughnessFactor": .9}},
        ],
        "buffers": [{"byteLength": len(raw)}], "bufferViews": views, "accessors": accessors,
        "extras": extras,
    }
    encoded = json.dumps(document).encode()
    encoded += b" "*((-len(encoded)) % 4)
    raw.extend(b"\x00"*((-len(raw)) % 4))
    return (struct.pack("<III", 0x46546C67, 2, 28+len(encoded)+len(raw))
            + struct.pack("<II", len(encoded), 0x4E4F534A)+encoded
            + struct.pack("<II", len(raw), 0x004E4942)+bytes(raw))


def to_glb(result):
    """Minimal glTF 2.0 visual mesh; Y-up, radius-unit scale is explicitly unmeasured."""
    points, faces = _display_arrays(result)
    return _encode_glb([(points, faces, 0)], [{"mesh": 0, "name": "Unmeasured master Y surface"}], [0],
                       {"units": result["units"], "engineering_approved": False}, VERSION)


def to_full_wheel_glb(result):
    """Sixfold visual assembly; repeated master geometry is not fused CAD."""
    points, faces = _display_arrays(result)
    sag = float(result["camera"]["sag"])
    radial = np.linalg.norm(points[:, [0, 2]], axis=1)
    source_root, source_tip = float(radial.min()), float(radial.max())
    target_root, target_tip = .18, .84
    mapped = target_root+(radial-source_root)*(target_tip-target_root)/max(source_tip-source_root, 1e-9)
    scale = mapped/np.maximum(radial, 1e-9)
    points[:, 0] *= scale
    points[:, 2] *= scale
    rim_inner, hub_outer = .82, .225
    context = _merge_meshes([
        _annular_prism(rim_inner, 1.0, .025, -.08),
        _annular_prism(.055, hub_outer, -sag+.012, -sag-.06, 96),
        _solid_cylinder(.098, -sag+.022, -sag-.01, 72),
    ])
    lug = _solid_cylinder(.021, .006, -.008, 40)
    groups = int(result["groups"])
    nodes = []
    for index in range(groups):
        angle = 2*math.pi*index/groups
        nodes.append({"mesh": 0, "name": f"Evidence master instance {index+1:02}",
                      "rotation": [0, math.sin(angle/2), 0, math.cos(angle/2)]})
    nodes.append({"mesh": 1, "name": "Assumed rim hub and center cap"})
    lug_front = -sag+.026
    for index in range(int(result["lug_count"])):
        angle = 2*math.pi*index/result["lug_count"]
        nodes.append({"mesh": 2, "name": f"Display-only lug recess {index+1:02}",
                      "translation": [.145*math.cos(angle), lug_front, -.145*math.sin(angle)]})
    result["full_wheel_preview"] = {
        "algorithm": FULL_WHEEL_VERSION, "generated": True, "groups": groups,
        "master_instances": groups, "geometry_instanced": True,
        "connected_or_fused": False, "rim_and_hub_are_assumed_context": True,
        "assumed_rim_inner_radius_R": rim_inner, "assumed_hub_outer_radius_R": hub_outer,
        "display_radial_remap": {"source_root_R": source_root, "source_tip_R": source_tip,
                                 "target_root_R": target_root, "target_tip_R": target_tip,
                                 "affects_local_validation": False},
        "lug_recesses_are_display_only": True, "engineering_approved": False,
        "note": "六等分实例用于整轮比例检查；试拼径向布局不参与局部误差计算，轮辋、中心盘和孔位是灰模上下文，未与母辐条融合，也未通过整轮轮廓验收。",
    }
    return _encode_glb([(points, faces, 0), (*context, 1), (*lug, 2)], nodes, list(range(len(nodes))),
                       {"units": result["units"], **result["full_wheel_preview"]}, FULL_WHEEL_VERSION)
