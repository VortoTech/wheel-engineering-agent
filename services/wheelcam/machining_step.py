"""Machining-level STEP: the features a first CNC programme cuts, built so the OCC booleans stay simple.

    PYTHONPATH=services .venv/bin/python -m wheelcam.machining_step RECIPE.json --out DIR [--spec spec.json]

The full style build (forged_blank.build) cuts lofted flanks, ridges, grooves and valleys into the
blank; on the real-order set its OCC booleans failed on 9 of 13 wheels (2026-09-27). This model keeps
only what turning, drilling and 2.5D milling make:
- the turned blank (the finished dish, no surfacing stock)
- the through windows as straight-walled prisms: the traced outlines united, clipped to the through
  radius the style build uses, opened to a 6 mm end mill's corner radius, one periodic spline each
- bolt holes with conical seats (the order's hole form), the centre bore from the blank
Spoke face surfaces, flanks, grooves and the pockets under the lip are left out; they are finishing
work for the factory CAM, and the GLB shows them. The report lists what is and is not in the STEP.
"""
import argparse
import json
import math
import time
from dataclasses import asdict, replace
from pathlib import Path

import cadquery as cq
import numpy as np

from .forged_blank import (TOOL_R, _robust_cut, blank, hole_form, lug_tools, recipe_from_dict, z_back, z_top)

SPLINE_STEP_MM = 3.0   # point spacing of a window spline: coarse enough that no edge is a sliver
MIN_WINDOW_MM2 = 40.0  # smaller islands of the opened footprint are dropped (a cutter cannot clear them)
SLIVER_MM2 = 5.0
MIN_HOLE_LAND_MM = 8.0  # least straight bolt hole left below the seat cone, down to the back face


def seat_land(p):
    """(seat_depth, note): the template seat depth, raised when less than MIN_HOLE_LAND_MM of straight
    hole would be left under the cone. On one real order the cone ended 0.1 mm above the sloped back
    and left a sliver face (2026-09-27); the seat depth is a template estimate, the hole form is not."""
    from .forged_blank import seat_cone_height
    h = seat_cone_height(p) if p.seat_cone_deg > 0 else 0.0
    back = max(z_back(p, r) for r in np.linspace(p.pcd / 2 - p.seat_d / 2, p.pcd / 2 + p.seat_d / 2, 25))
    land = p.hub_z - p.seat_depth - h - back
    if land >= MIN_HOLE_LAND_MM:
        return p.seat_depth, None
    depth = round(p.hub_z - h - back - MIN_HOLE_LAND_MM, 1)
    return depth, (f"锥座深度由模板 {p.seat_depth:g} mm 上提到 {depth:g} mm："
                   f"原深度下锥座底部到背面只剩 {land:.1f} mm 直孔，现保留 {MIN_HOLE_LAND_MM:g} mm。须工程师确认。")


def through_radius(p):
    """Outer radius of the through part of a window, as forged_blank.window_envelope_profile has it."""
    return max(p.ring_r - 2, min(p.window_through_r, p.barrel_inner_r - 3))


def window_loops(p):
    """Closed (x, y) loops of the through windows, machinable by a TOOL_R end mill."""
    import manifold3d as m3
    from .mesh_build import outlines
    loops = [np.asarray(o, float) for o in outlines(p)]
    loops = [o if _area(o) > 0 else o[::-1] for o in loops]
    union = m3.CrossSection.batch_boolean([m3.CrossSection([o]) for o in loops], m3.OpType.Add)
    clip = m3.CrossSection.circle(through_radius(p), 512) - m3.CrossSection.circle(p.hub_r + 2, 256)
    shape = (union ^ clip).offset(-TOOL_R, m3.JoinType.Round, 2, 64).offset(TOOL_R, m3.JoinType.Round, 2, 64)
    out = []
    for poly in shape.to_polygons():
        poly = np.asarray(poly, float)
        if abs(_area(poly)) < MIN_WINDOW_MM2:
            continue
        out.append(_resample(poly, SPLINE_STEP_MM))
    return out


def _area(xy):
    x, y = xy[:, 0], xy[:, 1]
    return .5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _resample(xy, step):
    closed = np.vstack([xy, xy[:1]])
    cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])
    n = max(12, int(cum[-1] / step))
    u = np.linspace(0, cum[-1], n, endpoint=False)
    return np.column_stack([np.interp(u, cum, closed[:, 0]), np.interp(u, cum, closed[:, 1])])


def window_prisms(p, loops):
    """One straight-walled prism per loop, through the whole web."""
    z0 = min(z_back(p, r) for r in np.linspace(p.hub_r, p.ring_r, 40)) - 20
    z1 = max(z_top(p, r) for r in np.linspace(p.hub_r, p.ring_r, 40)) + 20
    prisms = []
    for loop in loops:
        edge = cq.Edge.makeSpline([cq.Vector(x, y, z0) for x, y in loop], periodic=True)
        face = cq.Face.makeFromWires(cq.Wire.assembleEdges([edge]))
        prisms.append(cq.Solid.extrudeLinear(face, cq.Vector(0, 0, z1 - z0)))
    return prisms


def build(p):
    """(stock, part, stages). Each stage is one boolean family; a failure names its stage."""
    p = replace(p, face_crown_w=0.0)                  # finished dish: no surfacing stock on the front
    depth, note = seat_land(p)
    p = replace(p, seat_depth=depth)
    stock = blank(p)
    body, stages = stock, []

    def apply(name, tools):
        nonlocal body
        start, before = time.time(), body.Volume()
        try:
            body = _robust_cut(body, cq.Compound.makeCompound(tools), name)
        except RuntimeError:                          # one tool at a time is slower but sturdier
            for i, tool in enumerate(tools):
                body = _robust_cut(body, tool, f"{name}[{i}]")
        stages.append({"op": name, "tools": len(tools), "removed_mm3": round(before - body.Volume(), 1),
                       "valid": body.isValid(), "solids": len(body.Solids()), "seconds": round(time.time() - start, 1)})

    loops = window_loops(p)
    apply("through_windows", window_prisms(p, loops))
    apply("lug_holes_and_seats", lug_tools(replace(p, lug_pocket_d=0.0)))
    if note:
        stages[-1]["note"] = note
    return stock, body, stages


def export(recipe: dict, out, hole_form_text=None, et_mm=None) -> dict:
    """Build and write machining.step, stock.step and machining_report.json in `out`."""
    from .mass_properties import measure_volume
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in recipe.items() if not k.startswith("_")}
    form = hole_form(hole_form_text) if hole_form_text else {}
    data.update(form)
    p = recipe_from_dict(data)
    t = time.time()
    stock, part, stages = build(p)
    shift = cq.Vector(0, 0, p.width / 2)             # Z = 0 at the rim-width mid-plane, as export_model
    stock, part = stock.translate(shift), part.translate(shift)
    vol = measure_volume(part).volume_mm3
    step = out / "machining.step"
    cq.exporters.export(part, str(step))
    cq.exporters.export(stock, str(out / "stock.step"))
    back = cq.importers.importStep(str(step)).val()
    delta = abs(measure_volume(back).volume_mm3 - vol) / vol
    small = [f for f in part.Faces() if f.Area() < SLIVER_MM2]
    holes = [f for f in part.Faces() if f.geomType() == "CYLINDER" and
             math.isclose(f.radius() if hasattr(f, "radius") else 0, p.bolt_d / 2, abs_tol=.01)]
    checks = {
        "valid_single_solid": {"pass": part.isValid() and len(part.Solids()) == 1},
        "step_roundtrip": {"pass": back.isValid() and len(back.Solids()) == 1 and delta < 1e-3,
                           "relative_volume_delta": delta},
        "no_sliver_faces": {"pass": not small, "faces_under_5mm2": len(small)},
    }
    et = round(z_back(p, p.hub_r) + p.width / 2, 2)                 # mounting face above the mid-plane
    checks["offset_et"] = {"derived_mm": et, "expected_mm": et_mm, "pass": None if et_mm is None else abs(et - et_mm) < .5}
    report = {
        "schema": "wheelcam-machining-step-v1", "status": "not_released", "engineering_approved": False,
        "seconds": round(time.time() - t, 1), "volume_mm3": round(vol, 1),
        "mass_kg_6061": round(vol * 2700 / 1e9, 2), "face_count": len(part.Faces()),
        "windows": stages[0]["tools"],
        "hole_form": form or {"bolt_d": p.bolt_d, "seat_d": p.seat_d, "seat_cone_deg": p.seat_cone_deg,
                              "source": "recipe (no order hole form given)"},
        "checks": checks, "stages": stages,
        "adjustments": [s["note"] for s in stages if s.get("note")],
        "in_step": ["车削回转体（成品凹面）", "直壁通窗（Ø6 立铣刀圆角）", "螺栓孔与锥座", "中心孔"],
        "not_in_step": ["辐条正面曲面、脊线、槽和窗口斜面", "中心凹谷", "外圈盲槽", "倒角与圆角：交工厂 CAM"],
        "coordinates": "Z is the wheel axis, Z = 0 the rim-width mid-plane, +Z the face side; mm",
    }
    (out / "machining_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    (out / "recipe.json").write_text(json.dumps(asdict(replace(p, seat_depth=seat_land(p)[0])), ensure_ascii=False, indent=1))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe")
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="spec.json of a real order (its hole_form sets the bolt holes)")
    a = ap.parse_args()
    order = json.loads(Path(a.spec).read_text()) if a.spec else {}
    report = export(json.loads(Path(a.recipe).read_text()), a.out, order.get("hole_form"),
                    order.get("spec", {}).get("et_mm"))
    print(json.dumps({k: report[k] for k in ("seconds", "windows", "face_count", "checks")}, indent=1))


if __name__ == "__main__":
    main()
