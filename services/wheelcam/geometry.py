"""Deterministic concept geometry for template forged-monoblock-v15.

Spokes are either lofted sections (v9, spoke_method "loft") or a turned blank milled by window
outlines (spoke_method "window"). No image inference or manufacturing certification here; all
dimensions come from template.layout().
"""

import hashlib
import json
import math
import time
import zipfile
from pathlib import Path

import cadquery as cq
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS

from .models import Preparation, WheelSpec
from .mass_properties import measure_volume, volume as _volume, volume_method
from .preparation import check_preparation, valve_geometry, write_handoff_files
from .template import HUB_EDGE_FILLET, LUG_SEAT_THICKNESS, TEMPLATE_VERSION, layout, round_polygon
from .windows import rotate as rotate_outline, spoke_ridge_outlines


def _resample_closed(points, count: int) -> np.ndarray:
    """Evenly resample a closed XY polyline without changing its fitted source contour."""
    points = np.asarray(points, dtype=float)
    closed = np.vstack([points, points[0]])
    lengths = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    targets = np.linspace(0.0, cumulative[-1], count, endpoint=False)
    segments = np.minimum(np.searchsorted(cumulative, targets, side="right") - 1, len(points) - 1)
    fractions = (targets - cumulative[segments]) / np.maximum(lengths[segments], 1e-9)
    return closed[segments] + (closed[segments + 1] - closed[segments]) * fractions[:, None]


def _wire(polygon, radii, to3d) -> cq.Wire:
    edges = []
    for kind, *points in round_polygon(polygon, radii):
        vectors = [cq.Vector(*to3d(point)) for point in points]
        if kind == "line":
            edges.append(cq.Edge.makeLine(*vectors))
        else:
            edges.append(cq.Edge.makeThreePointArc(*vectors))
    return cq.Wire.assembleEdges(edges)


def _section_wire(frame) -> cq.Wire:
    (ox, oy), (xx, xy) = frame["origin"], frame["xdir"]
    # Sections sit on vertical planes: local u runs along xdir, local v is the global Z.
    return _wire(frame["polygon"], frame["radii"], lambda p: (ox + p[0] * xx, oy + p[0] * xy, p[1]))


def _loft(frames) -> cq.Solid:
    join = next((i for i,f in enumerate(frames) if f.get("window_join")), None)
    if join is not None:
        # Keep dense root sampling from distorting the long, thin outer arms.
        root = [{k:v for k,v in f.items() if k != "window_join"} for f in frames[:join+1]]
        tail = [{k:v for k,v in f.items() if k != "window_join"} for f in frames[join:]]
        return _loft(root).fuse(_loft(tail)).clean()
    wires = [_section_wire(frame) for frame in frames]
    if len(wires) > 5:
        builder = BRepOffsetAPI_ThruSections(True, False)
        builder.SetMaxDegree(3)
        for wire in wires:
            builder.AddWire(wire.wrapped)
        builder.Build()
        if not builder.IsDone():
            raise ValueError("骨架圆弧截面未能形成实体。")
        return cq.Solid(builder.Shape())
    return cq.Solid.makeLoft(wires, ruled=False)


def _profile_spoke(spec, lay):
    """Intersect an exact XY outline with a quadratic dished front/back slab."""
    upper = lay["explicit_profile"]["upper_segments_xy"]
    def edge(points, to3d):
        vectors = [cq.Vector(*to3d(p)) for p in points]
        return cq.Edge.makeLine(*vectors) if len(points)==2 else cq.Edge.makeBezier(vectors)
    first,last = upper[0][0],upper[-1][-1]
    segments = [[(first[0],-first[1]),first],*upper,[last,(last[0],-last[1])],
                *[[(x,-y) for x,y in reversed(segment)] for segment in reversed(upper)]]
    outline = cq.Wire.assembleEdges([edge(points,lambda p:(p[0],p[1],-500)) for points in segments])
    prism = cq.Solid.extrudeLinear(outline,[],cq.Vector(0,0,1000))
    f0,f1 = lay["sections"][0],lay["sections"][-1]
    r0,r1 = f0["r"],f1["r"]
    delta = f1["front"]-f0["front"]
    top = [(r0,f0["front"]),((r0+r1)/2,f0["front"]+delta*(1-spec.face_curve)/2),(r1,f1["front"])]
    bottom = [(r,z-depth) for (r,z),depth in zip(top,[f0["depth"],(f0["depth"]+f1["depth"])/2,f1["depth"]])]
    surface = [top,[top[-1],bottom[-1]],list(reversed(bottom)),[bottom[0],top[0]]]
    half_span = max(abs(y) for segment in upper for _,y in segment)+10
    wire = cq.Wire.assembleEdges([edge(points,lambda p:(p[0],-half_span,p[1])) for points in surface])
    slab = cq.Solid.extrudeLinear(wire,[],cq.Vector(0,2*half_span,0))
    return prism.intersect(slab).clean()


def _fuse_with_fillet(parts, radius, split_radius, attempts=None):
    """Fuse all parts, then round the new intersection edges (spoke↔hub inside split_radius, spoke↔rim outside).

    The rim side is the fragile one, so it steps down on its own before the hub side is touched.
    The radii actually applied are returned so the report states any downgrade rather than hiding it.
    """
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(parts[0].wrapped)
    for part in parts[1:]:
        tools.Append(part.wrapped)
    fuse = BRepAlgoAPI_Fuse()
    fuse.SetArguments(arguments)
    fuse.SetTools(tools)
    fuse.Build()
    if not fuse.IsDone():
        raise ValueError("轮辋、中心盘与轮辐未能合并为实体。")
    fused = fuse.Shape()
    hub_edges, rim_edges = [], []
    for shape in fuse.SectionEdges():
        edge = TopoDS.Edge_s(shape)
        center = cq.Edge(edge).Center()
        (hub_edges if math.hypot(center.x, center.y) < split_radius else rim_edges).append(edge)
    if attempts is None:
        attempts = [(1, 1), (1, 0.75), (1, 0.5), (1, 0.3), (1, 0), (0.5, 0)]
    attempts = [(radius * h, radius * r) for h, r in attempts] if radius > 0 else []
    for hub_radius, rim_radius in attempts:
        fillet = BRepFilletAPI_MakeFillet(fused)
        try:
            # Add() raises for edges not bounded by exactly two faces; Build() for unclosable fillets.
            for edge in hub_edges:
                fillet.Add(hub_radius, edge)
            for edge in rim_edges if rim_radius > 0 else ():
                fillet.Add(rim_radius, edge)
            fillet.Build()
        except Exception:
            continue
        if fillet.IsDone():
            candidate = cq.Shape.cast(fillet.Shape())
            if candidate.isValid() and len(candidate.Solids()) == 1:
                return candidate, {"hub": hub_radius, "rim": rim_radius}
    plain = cq.Shape.cast(fused)
    # The unfilleted fuse is only a fallback if it is itself sound; never hand an invalid body onward.
    if not plain.isValid() or len(plain.Solids()) != 1:
        raise ValueError("轮辋、中心盘与轮辐合并后不是单一有效实体。")
    return plain, {"hub": 0.0, "rim": 0.0}


WINDOW_FILLET_BUDGET_S = 60   # window edge fillet retries stop here; the worker allows 300 s per build
WINDOW_BACK_FILLET_MM = 1.0   # the back is not seen; a small break keeps the fillet surfaces simple
# Rim join for window-method centres: fewer junction radii, run in a child process that is killed after
# the timeout (one failing fillet attempt on photo #18 ran > 20 min, 2026-09-13).
WINDOW_JOIN_TIMEOUT_S = 45
# Window edge fillets (≤ 60 s budget) + join. Rounded rims make the join itself several times slower
# (133 s on photo #18), so wheels with many window edges simply run out of time and keep sharp rims;
# raising this to 150 s did not change that and cost 50 s per build (2026-09-17).
WINDOW_ROUND_JOIN_TIMEOUT_S = 100
WINDOW_JOIN_ATTEMPTS = [(1, 1), (1, 0.5), (1, 0), (0.5, 0)]


def _try_fillet(shape, pairs):
    """Round (edge, radius) pairs; None when OCCT cannot. Shape() of a failed fillet is null and crashes.

    Works on a copy: failed OCCT fillets widen tolerances of the input in place, and the input then
    fuses into an invalid two-solid body (photo #26, 2026-09-13). Edges map to the copy by position.
    """
    originals, work = shape.Edges(), shape.copy()
    copies = work.Edges()
    if len(copies) != len(originals):
        return None
    maker = BRepFilletAPI_MakeFillet(work.wrapped)
    try:
        for edge, radius in pairs:
            index = next(i for i, original in enumerate(originals) if original.isSame(edge))
            maker.Add(radius, copies[index].wrapped)
        maker.Build()
    except Exception:
        return None
    if not maker.IsDone():
        return None
    candidate = cq.Shape.cast(maker.Shape())
    return candidate if candidate.isValid() and len(candidate.Solids()) == 1 else None


def _window_outline_wire(outline, z=0.0) -> cq.Wire:
    """Actual periodic cutter curve, shared with the sector fidelity checks.

    The adapter interpolates the samples; it does not preserve an upstream
    analytic curve exactly. Callers must not treat polyline-only checks as a
    dimensional guarantee for this wire.
    """
    return cq.Wire.assembleEdges([cq.Edge.makeSpline(
        [cq.Vector(x, y, z) for x, y in outline], periodic=True)])


def _window_centre(spec: WheelSpec, lay, hub):
    """Turn hub and spoke blank as one body, then mill the window outlines through it (booleans only)."""
    blank = lay["window_blank"]
    top, bottom = blank["top_rz"], blank["bottom_rz"]
    at = lambda p: cq.Vector(p[0], 0, p[1])
    profile = cq.Wire.assembleEdges([
        cq.Edge.makeBezier([at(p) for p in top]), cq.Edge.makeLine(at(top[-1]), at(bottom[-1])),
        cq.Edge.makeBezier([at(p) for p in reversed(bottom)]), cq.Edge.makeLine(at(bottom[0]), at(top[0]))])
    turned = cq.Solid.revolve(profile, [], 360, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
    z_low, z_high = min(z for _, z in bottom) - 3, max(z for _, z in top) + 3
    keep = cq.Solid.makeCylinder(blank["hub_keep_radius_mm"], 2000, cq.Vector(0, 0, -1000))
    cutters = []
    contraction = spec.spoke_thickness_mm * math.tan(math.radians(spec.window_side_draft_deg))
    def offset_from_centre(points, distance):
        if abs(distance) <= 1e-9:
            return points
        centre = np.mean(np.asarray(points, dtype=float), axis=0)
        result = []
        for point in np.asarray(points, dtype=float):
            delta = point - centre
            length = float(np.linalg.norm(delta))
            result.append(tuple(centre + delta * max(0.0, 1 + distance / max(length, 1e-9))))
        return result
    def cutter(outline, angle):
        front = rotate_outline(outline, angle)
        front_wire = _window_outline_wire(front, z_high)
        if contraction <= 1e-9:
            return cq.Solid.extrudeLinear(front_wire, [], cq.Vector(0, 0, z_low - z_high))
        back = rotate_outline(offset_from_centre(outline, -contraction), angle)
        back_wire = _window_outline_wire(back, z_low)
        return cq.Solid.makeLoft([back_wire, front_wire], ruled=True)
    for index in range(spec.spoke_count):
        angle = spec.spoke_phase_deg + index * 360 / spec.spoke_count
        for outline in blank["outlines_mm"]:
            cutters.append(cutter(outline, angle).cut(keep))
    # Fuse the coaxial revolves first: cutting the blank alone and then fusing the hub left the hub
    # unattached with no boolean error in the 2026-09 spike.
    centre = hub.fuse(turned).clean().cut(*cutters).clean()
    if not centre.isValid() or len(centre.Solids()) != 1:
        raise ValueError("窗口切削后中心体不是单一有效实体，请检查窗口轮廓。")
    return centre.copy()   # a private body: failed fillets elsewhere must never touch it


def _window_face_relief(body, spec: WheelSpec, lay):
    """Mill a shallow shoulder around fitted windows after structural joins and fillets."""
    relief = spec.window_face_relief_mm
    if relief <= 0:
        return body
    blank = lay["window_blank"]
    top = blank["top_rz"]
    lower = [(r, z - relief) for r, z in top]
    at = lambda p: cq.Vector(p[0], 0, p[1])
    skin_profile = cq.Wire.assembleEdges([
        cq.Edge.makeBezier([at(p) for p in top]), cq.Edge.makeLine(at(top[-1]), at(lower[-1])),
        cq.Edge.makeBezier([at(p) for p in reversed(lower)]), cq.Edge.makeLine(at(lower[0]), at(top[0]))])
    skin = cq.Solid.revolve(skin_profile, [], 360, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
    z_low, z_high = min(z for _, z in lower) - 1, max(z for _, z in top) + 1
    keep = cq.Solid.makeCylinder(blank["hub_keep_radius_mm"], 2000, cq.Vector(0, 0, -1000))
    cutter_orbits = [[] for _ in blank["outlines_mm"]]
    for index in range(spec.spoke_count):
        angle = spec.spoke_phase_deg + index * 360 / spec.spoke_count
        for outline_index, outline in enumerate(blank["outlines_mm"]):
            # Structural cutouts keep the full fitted boundary.  The shallow
            # cosmetic shoulder uses a controlled 24-point guide: dense
            # pixel-scale splines make OCC booleans fragile without adding
            # visible face detail at wheel scale.
            points = _resample_closed(outline, 24)
            centre = points.mean(0)
            delta = points - centre
            lengths = np.linalg.norm(delta, axis=1)
            expanded = centre + delta * (1 + relief / np.maximum(lengths, 1e-9))[:, None]
            expanded = rotate_outline([tuple(point) for point in expanded], angle)
            wire = cq.Wire.assembleEdges([cq.Edge.makeSpline(
                [cq.Vector(x, y, z_high) for x, y in expanded], periodic=True)])
            prism = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, 0, z_low - z_high))
            cutter_orbits[outline_index].append(prism.intersect(skin).cut(keep))
    # OCC can return a null TopoDS shape when every curved cutter is submitted to
    # one boolean. Apply one symmetry orbit at a time and validate every step.
    result = body
    for orbit_index, orbit in enumerate(cutter_orbits, start=1):
        if any(cutter.isNull() or not cutter.isValid() for cutter in orbit):
            raise ValueError(f"第 {orbit_index} 类正面窗口浅斜面切削体无效，请减小斜面深度。")
        # Cut a complete rotational orbit together.  Besides being cheaper than
        # fifteen serial booleans this prevents accumulated tolerance drift from
        # making nominally identical sectors differ from one another.
        result = result.cut(*orbit).clean()
        solids = result.Solids() if not result.isNull() else []
        # Numerical debris from a tangent curved cut can have signed volumes on
        # the order of 1e-8 mm3.  It must not be counted as a physical component.
        meaningful_volume = 1e-4
        positive = [solid for solid in solids if solid.Volume() > meaningful_volume]
        # OpenCascade sometimes exposes the newly cut internal cavity as a
        # second, negatively oriented solid.  That is not a detached component:
        # retain the sole positive body, but never silently discard two positive
        # bodies (which would mean that a shoulder really severed the wheel).
        if len(positive) == 1 and all(
                s.Volume() <= meaningful_volume for s in solids if s is not positive[0]):
            result = positive[0].fix().clean()
            solids = [result]
        if result.isNull() or not result.isValid() or len(solids) != 1:
            volumes = [round(s.Volume(), 3) for s in solids]
            raise ValueError(
                f"第 {orbit_index} 类正面窗口浅斜面生成后不是单一有效实体"
                f"（实体体积 {volumes}），请减小斜面深度。")
    return result


def _window_spoke_ridges(body, spec: WheelSpec, lay):
    """Add two shallow positive-face bands derived from each group's solid window webs."""
    height = spec.window_spoke_ridge_mm
    if height <= 0:
        return body, {"requested_mm": 0.0, "applied_mm": 0.0, "master_ridge_count": 0}
    blank = lay["window_blank"]
    master = spoke_ridge_outlines(blank["outlines_mm"], spec.spoke_count)
    if len(master) != 2:
        raise ValueError("正面双辐脊线需要每组 3 个可分离窗口，请先检查窗口拟合。")
    top = blank["top_rz"]
    lower = [(r, z - 0.25) for r, z in top]
    upper = [(r, z + height) for r, z in top]
    at = lambda p: cq.Vector(p[0], 0, p[1])
    skin_profile = cq.Wire.assembleEdges([
        cq.Edge.makeBezier([at(p) for p in upper]), cq.Edge.makeLine(at(upper[-1]), at(lower[-1])),
        cq.Edge.makeBezier([at(p) for p in reversed(lower)]), cq.Edge.makeLine(at(lower[0]), at(upper[0]))])
    skin = cq.Solid.revolve(skin_profile, [], 360, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
    z_low, z_high = min(z for _, z in lower) - 1, max(z for _, z in upper) + 1
    ribs = []
    for group in range(spec.spoke_count):
        angle = spec.spoke_phase_deg + group * 360 / spec.spoke_count
        for outline in master:
            rotated = rotate_outline(outline, angle)
            vectors = [cq.Vector(x, y, z_low) for x, y in rotated]
            wire = cq.Wire.assembleEdges([
                cq.Edge.makeLine(vectors[index], vectors[(index + 1) % len(vectors)])
                for index in range(len(vectors))])
            prism = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, 0, z_high - z_low))
            rib = prism.intersect(skin)
            if rib.isNull() or not rib.isValid():
                raise ValueError("正面双辐脊线切削体无效，请减小脊线高度。")
            ribs.append(rib)
    result = body.fuse(*ribs).clean()
    if result.isNull() or not result.isValid() or len(result.Solids()) != 1:
        raise ValueError("正面双辐脊线未能与轮毂融合为单一有效实体，请减小脊线高度。")
    return result, {"requested_mm": height, "applied_mm": height, "master_ridge_count": len(master)}


def _window_rim_edges(centre, top, bottom, hub_radius):
    """(edge, is_front) for each window rim: spline-wall ∩ turned-face curves outside the hub."""
    rs, fronts, backs = [p[0] for p in top], [p[1] for p in top], [p[1] for p in bottom]
    edges = []
    for edge in centre.Edges():
        # Circles and Bezier seams belong to the turning; window rims are B-splines.
        center = edge.Center()
        radial = math.hypot(center.x, center.y)
        if edge.geomType() != "BSPLINE" or radial < hub_radius + 6:
            continue
        mid = (float(np.interp(radial, rs, fronts)) + float(np.interp(radial, rs, backs))) / 2
        edges.append((edge, center.z > mid))
    return edges


def _edge_key(edge):
    """Position key, stable while an edge is untouched by neighbouring fillets."""
    centre = edge.Center()
    return (round(centre.x, 1), round(centre.y, 1), round(centre.z, 1))


def round_window_edges(centre, top, bottom, hub_radius, requested):
    """Round the window rims; (shape, info).

    One batch over every rim edge fails whenever any pair interferes, and photos #18/#20/#21 then ended
    up with nothing rounded even though single-edge trials succeeded on 34 of 44 edges (2026-09-13). So
    the rims are rounded a window at a time, each onto the result of the last: a window that OCCT
    refuses costs only itself. Edges are re-found on the updated shape by position, since filleting
    rebuilds the topology.

    Runs in the join child: an OCCT fillet can run for many minutes (photo #20) and only a process can
    be killed.
    """
    edges = _window_rim_edges(centre, top, bottom, hub_radius)
    info = {"window_edge_fillet_requested_mm": requested, "window_edge_fillet_applied_mm": 0.0,
            "window_edges_rounded": 0, "window_edges_total": len(edges), "window_fillet_budget_hit": False}
    if requested <= 0 or not edges:
        return centre, info
    radius_for = lambda front: requested if front else min(WINDOW_BACK_FILLET_MM, requested)
    shape, done, rounded_count = centre, set(), 0
    deadline = time.monotonic() + WINDOW_FILLET_BUDGET_S
    while time.monotonic() < deadline:
        current = [(edge, front) for edge, front in _window_rim_edges(shape, top, bottom, hub_radius)
                   if _edge_key(edge) not in done]
        if not current:
            break
        # One window's rim: the connected run of edges sharing vertices with the first one.
        group, pool = [current[0]], current[1:]
        while True:
            vertices = {v.toTuple() for edge, _ in group for v in edge.Vertices()}
            nearby = [pair for pair in pool if any(v.toTuple() in vertices for v in pair[0].Vertices())]
            if not nearby:
                break
            group += nearby
            pool = [pair for pair in pool if pair not in nearby]
        attempt = _try_fillet(shape, [(edge, radius_for(front)) for edge, front in group])
        if attempt is None and len(group) > 1:
            # Salvage the window edge by edge rather than losing the whole rim.
            for edge, front in group:
                single = _try_fillet(shape, [(edge, radius_for(front))])
                if single is not None:
                    shape, rounded_count = single, rounded_count + 1
                    done.add(_edge_key(edge))
                if time.monotonic() > deadline:
                    break
            done.update(_edge_key(edge) for edge, _ in group)
            continue
        done.update(_edge_key(edge) for edge, _ in group)
        if attempt is not None:
            shape, rounded_count = attempt, rounded_count + len(group)
    if time.monotonic() >= deadline:
        info["window_fillet_budget_hit"] = True
    if rounded_count:
        info.update(window_edge_fillet_applied_mm=requested, window_edges_rounded=rounded_count)
    return shape, info


def _join_window_centre(spec: WheelSpec, lay, rim, hub):
    """Milled centre ↔ rim. Each fillet variant runs in a child process that is killed on timeout:

    1. round the window edges, then join with junction fillets     (WINDOW_ROUND_JOIN_TIMEOUT_S)
    2. join with junction fillets, window edges left sharp          (WINDOW_JOIN_TIMEOUT_S)
    3. last resort, in-process: plain join, no fillets (booleans only; 18 s on photo #18)

    Junction fillets carry load, window edge breaks do not: variant 2 replaces 1 when it gets the larger
    junction fillet. The report states what was applied.
    """
    from .join_worker import join_with_timeout
    plain = _window_centre(spec, lay, hub)
    blank = lay["window_blank"]
    split = (lay["hub_radius"] + lay["well_radius"]) / 2
    sharp = {"window_edge_fillet_requested_mm": spec.window_edge_fillet_mm, "window_edge_fillet_applied_mm": 0.0,
             "window_edges_rounded": 0,
             "window_edges_total": len(_window_rim_edges(plain, blank["top_rz"], blank["bottom_rz"], lay["hub_radius"]))}
    join = {"radius": spec.junction_fillet_mm, "split": split, "attempts": WINDOW_JOIN_ATTEMPTS}
    variants = []
    if spec.window_edge_fillet_mm > 0:
        variants.append(({**join, "round": {"top": blank["top_rz"], "bottom": blank["bottom_rz"],
                                            "hub_radius": lay["hub_radius"], "fillet": spec.window_edge_fillet_mm}},
                         WINDOW_ROUND_JOIN_TIMEOUT_S))
    variants.append((join, WINDOW_JOIN_TIMEOUT_S))
    best = None
    for params, timeout in variants:
        joined = join_with_timeout(rim, plain, params, timeout)
        if joined is None:
            continue
        body, applied, rounding = joined
        if best is None or min(applied.values()) > min(best[1].values()):
            best = (body, applied, {**sharp, **rounding})
        if min(applied.values()) >= spec.junction_fillet_mm:
            break
    if best is None:
        try:
            body, applied = _fuse_with_fillet([rim, plain], 0, split)
        except ValueError:
            raise ValueError("窗口法中心体未能与轮辋合并为有效实体，请检查窗口轮廓。") from None
        best = (body, applied, sharp)
    return best


def _cutters(spec: WheelSpec, lay) -> list[cq.Solid]:
    bottom, top = spec.offset_et_mm - 5, lay["hub_front_z"] + 5
    cutters = [cq.Solid.makeCylinder(spec.center_bore_mm / 2, top - bottom, cq.Vector(0, 0, bottom))]
    seat_z = spec.offset_et_mm + LUG_SEAT_THICKNESS
    hole_r, cone_r = spec.bolt_diameter_mm / 2, lay["lug_cone_diameter"] / 2
    cone_height = (cone_r - hole_r) / math.tan(math.radians(30))  # 60° included seat
    for index in range(spec.bolt_count):
        angle = 2 * math.pi * index / spec.bolt_count
        x, y = spec.bolt_circle_mm / 2 * math.cos(angle), spec.bolt_circle_mm / 2 * math.sin(angle)
        cutters += [
            cq.Solid.makeCylinder(hole_r, top - bottom, cq.Vector(x, y, bottom)),
            cq.Solid.makeCone(hole_r, cone_r, cone_height, cq.Vector(x, y, seat_z - cone_height)),
            cq.Solid.makeCylinder(lay["lug_pocket_diameter"] / 2, top - seat_z, cq.Vector(x, y, seat_z)),
        ]
    return cutters


def _rim_pocket_band(rim, lay):
    """Add a front band and mill an independent cyclic array of blind pockets."""
    band = lay["rim_pocket_band"]
    if not band:
        return rim
    ring = (cq.Workplane("XY", origin=(0, 0, band["back_z_mm"]))
            .circle(band["outer_radius_mm"]).circle(band["inner_radius_mm"])
            .extrude(band["front_z_mm"] - band["back_z_mm"]).val())
    result = rim.fuse(ring).clean()
    if not result.isValid() or len(result.Solids()) != 1:
        raise ValueError("外圈槽承载环未能与轮辋形成单一有效实体。")
    cutters = []
    z0 = band["front_z_mm"] - band["depth_mm"]
    for index in range(band["count"]):
        cutter = (cq.Workplane("XY", origin=(band["pocket_center_radius_mm"], 0, z0))
                  .rect(band["radial_length_mm"], band["tangential_width_mm"])
                  .extrude(band["depth_mm"] + 1)
                  .edges("|Z").fillet(band["corner_radius_mm"]).val())
        cutters.append(cutter.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1),
                                     band["phase_deg"] + index * 360 / band["count"]))
    result = result.cut(*cutters).clean()
    if not result.isValid() or len(result.Solids()) != 1:
        raise ValueError("外圈槽切削后轮辋不是单一有效实体，请调整槽尺寸或数量。")
    return result


def _build_wheel(spec: WheelSpec):
    lay = layout(spec)
    points, radii = zip(*lay["rim_polygon"])
    rim_wire = _wire(points, radii, lambda p: (p[0], 0.0, p[1]))
    rim = cq.Solid.revolve(rim_wire, [], 360, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
    if lay["front_lip"]:
        lip = lay["front_lip"]
        wire = _wire(lip["polygon"], lip["radii"], lambda p: (p[0], 0.0, p[1]))
        lip_shape = cq.Solid.revolve(wire, [], 360, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
        rim = rim.fuse(lip_shape).clean()
        if not rim.isValid() or len(rim.Solids()) != 1:
            raise ValueError("加宽轮唇未与轮辋形成有效连接。")
    rim = _rim_pocket_band(rim, lay)
    hub = (cq.Workplane("XY", origin=(0, 0, spec.offset_et_mm))
           .circle(lay["hub_radius"]).extrude(spec.hub_thickness_mm)
           .edges(">Z").fillet(HUB_EDGE_FILLET).val())
    window_info = {}
    if lay["window_blank"]:
        spoke = None
        body, applied, window_info = _join_window_centre(spec, lay, rim, hub)
        body = _window_face_relief(body, spec, lay)
        body, ridge_info = _window_spoke_ridges(body, spec, lay)
        window_info["spoke_ridge"] = ridge_info
    else:
        spoke = _profile_spoke(spec,lay) if lay["explicit_profile"] else _loft(lay["sections"])
    if lay["paired_slot"]:
        slot = lay["paired_slot"]
        r, a, b = slot["radius_mm"], slot["start_r_mm"], slot["end_r_mm"]
        if spec.paired_gap_flare_mm or spec.paired_root_round_mm:
            corner = slot["corner_radius_mm"]
            q, m, tip = slot["flare_start_r_mm"], slot["flare_end_r_mm"], slot["gap_mm"]/2
            path = cq.Workplane("XY", origin=(0, 0, -500)).moveTo(a+corner, -r)
            if q > a+corner+1e-7:
                path = path.lineTo(q, -r)
            path = (path.bezier([(q+(m-q)/3, -r), (m-(m-q)/3, -tip), (m, -tip)], includeCurrent=True)
                    .lineTo(b, -tip).lineTo(b, tip).lineTo(m, tip)
                    .bezier([(m-(m-q)/3, tip), (q+(m-q)/3, r), (q, r)], includeCurrent=True))
            if q > a+corner+1e-7:
                path = path.lineTo(a+corner, r)
            k = corner/math.sqrt(2)
            path = path.threePointArc((a+corner-k, r-corner+k), (a, r-corner))
            if r-corner > 1e-7:
                path = path.lineTo(a, -r+corner)
            cutter = (path.threePointArc((a+corner-k, -r+corner-k), (a+corner, -r))
                      .close().extrude(1000).val())
        else:
            cutter = (cq.Workplane("XY", origin=(0, 0, -500)).moveTo(a + r, -r)
                      .lineTo(b, -r).lineTo(b, r).lineTo(a + r, r)
                      .threePointArc((a, 0), (a + r, -r)).close().extrude(1000).val())
        spoke = spoke.cut(cutter).clean()
    if lay["pockets"]:
        spoke = spoke.cut(_loft(lay["pockets"]))
    spokes = [] if spoke is None else [
        spoke.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), spec.spoke_phase_deg + index * 360 / spec.spoke_count)
        for index in range(spec.spoke_count)]
    # Shared webs intentionally create spoke/spoke intersections. Applying the old
    # bulk junction fillet to these edges is both incorrect and very expensive.
    # Their rounded planform is already in the loft; report the 3D fillet as absent.
    if spoke is not None:
        body, applied = _fuse_with_fillet([rim, hub, *spokes],
                                          0 if lay["interspoke_window"] else spec.junction_fillet_mm,
                                          (lay["hub_radius"] + lay["well_radius"]) / 2)
    result = body.cut(*_cutters(spec, lay)).clean()
    if spec.valve_diameter_mm:
        center, axis, length = valve_geometry(spec, lay)
        direction = cq.Vector(*axis)
        start = cq.Vector(*center) - direction * (length / 2)
        end = cq.Vector(*center) + direction * (length / 2)
        solid = result.Solids()[0]
        if not solid.isInside(tuple(center)) or solid.isInside(start.toTuple()) or solid.isInside(end.toTuple()):
            raise ValueError("气门孔未正确跨越轮辋槽底，请调整孔方向。")
        result = result.cut(cq.Solid.makeCylinder(spec.valve_diameter_mm / 2, length, start, direction)).clean()
        if result.Solids()[0].isInside(tuple(center)):
            raise ValueError("气门孔未能切穿轮辋。")
    return cq.Workplane(obj=result), {"junction_fillet_requested_mm": spec.junction_fillet_mm,
                                      "junction_fillet_applied_mm": min(applied.values()),
                                      "hub_fillet_applied_mm": applied["hub"],
                                      "rim_fillet_applied_mm": applied["rim"], **window_info}, rim


def build_wheel(spec: WheelSpec) -> tuple[cq.Workplane, dict]:
    wheel, info, _ = _build_wheel(spec)
    return wheel, info


def inspect_shape(shape, spec: WheelSpec) -> dict:
    solids = shape.Solids()
    bbox = shape.BoundingBox()
    measurement = measure_volume(shape)
    volume = measurement.volume_mm3
    derived = layout(spec)["derived"]
    dimensions = [bbox.xlen, bbox.ylen, bbox.zlen]
    expected = [derived["outer_diameter_mm"], derived["outer_diameter_mm"], derived["overall_width_mm"]]
    errors = [abs(actual - target) for actual, target in zip(dimensions, expected)]
    checks = {
        "valid_brep": bool(shape.isValid()),
        "single_solid": len(solids) == 1,
        "positive_volume": volume > 0,
        "envelope_matches": max(errors) < 0.001,
    }
    if not all(checks.values()):
        raise ValueError(f"实体检查未通过：{checks}")
    return {
        "checks": checks,
        "solid_count": len(solids),
        "volume_mm3": round(volume, 3),
        "volume_measurement": measurement.to_dict(),
        "bbox_mm": [round(value, 4) for value in dimensions],
        "max_envelope_error_mm": round(max(errors), 8),
        "face_count": len(shape.Faces()),
        "derived": derived,
    }


STEP_ROUNDTRIP_RELATIVE_LIMIT = 5e-5
STEP_STABILITY_FILLET_FACTORS = (0.75, 0.5, 0.3, 0.0)


def _export_step_roundtrip(shape, path: Path) -> tuple[cq.Shape, float]:
    """Write and reopen STEP, measuring the serialized solid rather than the in-memory B-Rep."""
    cq.exporters.export(shape, str(path))
    imported = cq.importers.importStep(str(path)).val()
    source_volume = _volume(shape.val() if isinstance(shape, cq.Workplane) else shape)
    relative_delta = abs(_volume(imported) - source_volume) / source_volume
    return imported, relative_delta


def export_model(spec: WheelSpec, output: Path, preparation: Preparation | None = None, snapshot: dict | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    resolved_spec = spec
    wheel, build_info, rim = _build_wheel(spec)
    report = inspect_shape(wheel.val(), spec)
    preparation = preparation or Preparation()
    snapshot = snapshot or {"spec": spec.model_dump(), "template_version": TEMPLATE_VERSION,
                            "preparation": preparation.model_dump()}
    if not (output / "recipe.json").exists():
        (output / "recipe.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    step_path = output / "wheel.step"
    # Reimport checks the serialized artifact, independently of the in-memory object. Some otherwise
    # valid OCCT fillet surfaces move measurably when represented as STEP B-splines. In that case,
    # rebuild with a smaller junction fillet and record the degradation instead of weakening the gate.
    imported, relative_delta = _export_step_roundtrip(wheel, step_path)
    stability_attempts = [{
        "junction_fillet_mm": build_info["junction_fillet_applied_mm"],
        "step_volume_relative_delta": relative_delta,
    }]
    requested_junction_fillet = spec.junction_fillet_mm
    if relative_delta > STEP_ROUNDTRIP_RELATIVE_LIMIT and requested_junction_fillet > 0:
        for factor in STEP_STABILITY_FILLET_FACTORS:
            candidate_spec = spec.model_copy(update={
                "junction_fillet_mm": requested_junction_fillet * factor,
            })
            candidate_wheel, candidate_info, candidate_rim = _build_wheel(candidate_spec)
            candidate_imported, candidate_delta = _export_step_roundtrip(candidate_wheel, step_path)
            stability_attempts.append({
                "junction_fillet_mm": candidate_info["junction_fillet_applied_mm"],
                "step_volume_relative_delta": candidate_delta,
            })
            if candidate_delta <= STEP_ROUNDTRIP_RELATIVE_LIMIT:
                resolved_spec = candidate_spec
                wheel, build_info, rim = candidate_wheel, candidate_info, candidate_rim
                imported, relative_delta = candidate_imported, candidate_delta
                build_info["junction_fillet_requested_mm"] = requested_junction_fillet
                report = inspect_shape(wheel.val(), resolved_spec)
                break
    reopened = inspect_shape(imported, resolved_spec)
    # STEP stores B-spline surfaces at geometric tolerance.  The P1 matrix found a valid 350-face
    # lofted wheel at 2.5543e-5 relative volume delta; a fitted two-ridge face measured 3.07e-5.
    # Keep one explicit 5e-5 interchange bound for both instead of a sample-specific exception.
    if relative_delta > STEP_ROUNDTRIP_RELATIVE_LIMIT:
        raise ValueError(
            f"STEP 导出回读后的体积不一致（{relative_delta:.7%} > "
            f"{STEP_ROUNDTRIP_RELATIVE_LIMIT:.7%}）。"
        )
    report["checks"]["step_roundtrip"] = True
    report["step_volume_relative_delta"] = relative_delta
    report["step_volume_relative_limit"] = STEP_ROUNDTRIP_RELATIVE_LIMIT
    report["step_volume_method"] = volume_method()
    report["step_volume_measurement"] = reopened.get("volume_measurement")
    report["step_solid_count"] = reopened["solid_count"]
    report["step_stability"] = {
        "fallback_used": len(stability_attempts) > 1,
        "attempts": stability_attempts,
    }
    from .appearance import export_previews
    report["presentation"] = export_previews(wheel, rim, resolved_spec, output)
    window = spec.spoke_method == "window"
    # Window-method models can contain dozens of spline edges. OCCT's SVG hidden-line projection may
    # run for many minutes after STEP/GLB already succeeded, so the browser uses the actual GLB for
    # its fitted-camera overlay. Keep SVG only for the lighter lofted paired-spoke fallback view.
    if spec.spoke_style == "paired" and not window:
        # No perspective: a transparent front projection aligned to the actual CAD bounding box.
        cq.exporters.export(wheel, str(output / "front.svg"), opt={"width": 1000, "height": None,
            "marginLeft": 0, "marginTop": 0, "projectionDir": (0, 0, 1), "showAxes": False,
            "showHidden": False, "strokeColor": (255, 195, 80), "strokeWidth": 0.4})
    limitations = ["轮辋截面为近似 J 型轮缘、5° 胎圈座深槽轮辋，未逐项核对 ETRTO / TRA 标准",
                   "气门孔仅为通孔，气门嘴密封座、平衡配重面与中心盖安装结构尚未验证" if spec.valve_diameter_mm else "未启用气门孔；平衡配重面与中心盖安装结构尚未包含",
                   "公差、载荷及输入资料尚未作工程审核；包络检查和重量均依赖当前版本输入",
                   "未进行结构分析、CAM 或机床验证"]
    if build_info["junction_fillet_applied_mm"] < spec.junction_fillet_mm:
        limitations.insert(0, f"轮辐连接圆角请求 {spec.junction_fillet_mm:g} mm，实际生成：中心盘侧 "
                              f"{build_info['hub_fillet_applied_mm']:g} mm，轮辋侧 {build_info['rim_fillet_applied_mm']:g} mm")
    report.update(build_info)
    if window:
        blank = layout(resolved_spec)["window_blank"]
        report["window_method"] = {key: blank[key] for key in ("window_count", "min_web_mm", "open_area_mm2", "scope")}
        report["window_method"]["side_draft_deg"] = spec.window_side_draft_deg
        report["window_method"]["face_relief_mm"] = spec.window_face_relief_mm
        report["window_method"]["spoke_ridge"] = build_info.get("spoke_ridge", {
            "requested_mm": spec.window_spoke_ridge_mm, "applied_mm": 0.0, "master_ridge_count": 0})
        report["window_method"]["back_contraction_mm"] = round(
            spec.spoke_thickness_mm * math.tan(math.radians(spec.window_side_draft_deg)), 3)
        report["rotational_symmetry"] = {
            "status": "constructed_from_one_master",
            "groups": spec.spoke_count,
            "period_deg": 360 / spec.spoke_count,
            "master_window_count": len(spec.window_outlines_mm),
            "scope": "同一组母窗口由精确角度旋转复制；相机透视只改变屏幕投影，不改变实体尺寸",
        }
        if build_info["window_edge_fillet_applied_mm"] < spec.window_edge_fillet_mm or \
                build_info["window_edges_rounded"] < build_info["window_edges_total"]:
            limitations.insert(0, f"窗口棱边圆角请求 {spec.window_edge_fillet_mm:g} mm，实际 "
                                  f"{build_info['window_edge_fillet_applied_mm']:g} mm，"
                                  f"{build_info['window_edges_rounded']}/{build_info['window_edges_total']} 条棱边已倒圆")
        limitations.insert(0, f"窗口法：正面窗口来自输入草图，具体证据来源见配方；双辐脊线 {spec.window_spoke_ridge_mm:g} mm、正面浅斜面 {spec.window_face_relief_mm:g} mm、侧壁拔模 {spec.window_side_draft_deg:g}°、前后曲面与厚度沿用模板假设，非实测")
    if spec.rim_pocket_count:
        band = layout(resolved_spec)["rim_pocket_band"]
        report["rim_pockets"] = {key: band[key] for key in ("count", "phase_deg", "radial_length_mm",
            "tangential_width_mm", "depth_mm", "floor_skin_mm", "scope")}
        limitations.insert(0, "独立外圈槽按商品图可见比例建立；槽深、底部余量和背面结构为模板假设，未作强度或加工验证")
    if spec.spoke_style == "paired" and not window:
        lay = layout(resolved_spec)
        report["skeleton"] = {"window":lay["interspoke_window"], "explicit_profile":lay["explicit_profile"], "stations":lay["skeleton_stations"],
            "note":("此模式的正面边界宽度；侧壁竖直，前后厚度沿用假设，非实测或最小壁厚评估。" if spec.paired_blade_root_mm else "当前版本的截面控制宽度，未扣除棱边圆角；不是实体最小厚度。前后厚度沿用假设。")}
    if spec.paired_blade_root_mm and not window:
        report["spoke_fillet_requested_mm"] = spec.spoke_fillet_mm
        report["spoke_fillet_applied_mm"] = 0.0
        limitations.insert(0, "直顺支臂采用精确正面边界与假设凹面厚度相交；侧壁竖直，棱边圆角暂未生成，非原拔模截面")
    if spec.paired_window_root_mm and not window:
        limitations.insert(0, ("大窗口圆弧通过显式正面边界形成相邻组连接" if spec.paired_blade_root_mm else "大窗口圆弧通过密集截面形成相邻组连接") + "，非恒定半径倒圆；顶面与背面连续性、最小壁厚及结构强度仍待工程验证")
    if spec.spoke_style == "paired" and not window:
        limitations.insert(0, "双辐为照片人工拟合的单片近似；分体连接、中心盖和周圈螺栓仅外观展示，不参与工程检查")
    if spec.lip_extension_mm:
        limitations.insert(0, "加宽轮唇和展开轮辐来自单张照片比例拟合；轮唇背部截面与厚度为假设，非实物尺寸恢复")
    from .build_resolution import resolve_build
    from .engineering_schema import build_engineering_definition
    resolution = resolve_build(spec, resolved_spec, report)
    report["build_status"] = resolution.status
    report["build_resolution"] = resolution.model_dump(mode="json")
    if resolution.review_required:
        limitations.insert(0, "实际构造未完全满足请求或存在未核实特征；须复核 build_resolution，几何通过不代表设计要求通过。")
    # Emit evidence and actual outcomes only after the final STEP candidate is accepted.
    engineering = build_engineering_definition(spec, snapshot.get("sources", {}), build_resolution=resolution)
    (output / "engineering.json").write_text(engineering.model_dump_json(indent=2))
    report["preparation"] = check_preparation(wheel.val(), resolved_spec, preparation, output)
    report["handoff"] = write_handoff_files(output, resolved_spec, snapshot, build_resolution=report["build_resolution"])
    report["model_id"] = snapshot.get("model_id")
    report["draft_revision"] = snapshot.get("draft_revision")
    report.update({
        "template_version": TEMPLATE_VERSION,
        "units": "mm", "coordinates": "右手系，轮毂轴线为 Z，轮辋宽度中面 Z=0，+Z 为外侧（装饰面）",
        "status": "geometry_checked", "engineering_approved": False,
        "limitations": limitations,
        "artifacts": {name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                              "bytes": (output / name).stat().st_size}
                      for name in ["wheel.step", "wheel.glb", "recipe.json", "engineering.json", "features.json", "operations.csv",
                                   "stock.step", "caliper-envelope.step", "presentation.glb", "front.svg"] if (output / name).exists()},
    })
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output / "handoff.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in [*report["artifacts"], "report.json"]:
            archive.write(output / name, arcname=name)
    return report
