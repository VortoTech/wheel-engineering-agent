"""Review-only process study sourced from an existing machining-level STEP pair.

The STEP files are never regenerated here. Tessellation and rasterisation are
approximations for display and sampled stock removal, not factory CAM checks.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import replace
from pathlib import Path

import cadquery as cq
import manifold3d as m3
import numpy as np
from PIL import Image, ImageDraw

from .forged_blank import face_z, recipe_from_dict, z_back, z_top
from .machining_step import through_radius, window_loops
from .manufacturing_demo import (
    GRID_MM, STEP_DOWN_MM, STEP_OVER_MM, TOOL_D_MM, _continuous_cutter,
    _reference_nc, _render_3d_comparison, _sha, _tool_rows, raster_sweeps,
)
from .mesh_build import export_glb


def _step_mesh(path: Path):
    """Read the actual STEP and weld OCC's separate face meshes for Manifold."""
    import trimesh

    shape = cq.importers.importStep(str(path)).val()
    if not shape.isValid() or len(shape.Solids()) != 1:
        raise ValueError(f"Invalid or multi-solid STEP: {path}")
    vertices, faces = shape.tessellate(0.1, 0.1)
    tri = trimesh.Trimesh(vertices=[[v.x, v.y, v.z] for v in vertices],
                          faces=faces, process=True)
    if not tri.is_watertight:
        raise ValueError(f"Non-watertight STEP tessellation: {path}")
    body = m3.Manifold(m3.Mesh(np.asarray(tri.vertices, dtype=np.float32),
                               np.asarray(tri.faces, dtype=np.uint32)))
    if body.status() != m3.Error.NoError:
        raise ValueError(f"STEP tessellation failed: {path}: {body.status()}")
    return shape, body


def _window_grid(p, loops, grid_mm=GRID_MM):
    extent = math.ceil((p.lip_r + 8) / grid_mm) * grid_mm
    axis = np.arange(-extent, extent + grid_mm / 2, grid_mm)
    bitmap = Image.new("1", (len(axis), len(axis)), 0)
    painter = ImageDraw.Draw(bitmap)
    for loop in loops:
        painter.polygon([((x - axis[0]) / grid_mm, (y - axis[0]) / grid_mm)
                         for x, y in loop], fill=1)
    target = np.asarray(bitmap, dtype=bool).copy()
    xx, yy = np.meshgrid(axis, axis)
    target &= xx * xx + yy * yy <= through_radius(p) ** 2
    return axis, target


def create_step_package(machining_dir: Path, output: Path, *, spec_path: Path | None = None) -> dict:
    """Build a reproducible study from stock.step and machining.step in one case."""
    machining_dir, output = Path(machining_dir), Path(output)
    inputs = {name: machining_dir / name for name in
              ("stock.step", "machining.step", "recipe.json", "machining_report.json")}
    missing = [name for name, path in inputs.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing machining STEP inputs: {missing}")
    report = json.loads(inputs["machining_report.json"].read_text())
    if report.get("schema") != "wheelcam-machining-step-v1" or any(
            check.get("pass") is not True for check in report.get("checks", {}).values()):
        raise ValueError("Machining STEP report has failed or missing verification checks")
    if not report.get("checks"):
        raise ValueError("Machining STEP report has no verification checks")
    recipe = json.loads(inputs["recipe.json"].read_text())
    p = recipe_from_dict(recipe)
    # The exported STEP builder suppresses face crown and may adjust seat depth.
    p = replace(p, face_crown_w=0.0)
    if spec_path:
        spec = json.loads(Path(spec_path).read_text())
        order = spec["spec"]
        for key, actual, expected in (("pcd", p.pcd, order["pcd_mm"]),
                                      ("bolts", p.bolts, order["bolts"]),
                                      ("center bore", 2 * p.center_bore_r, order["center_bore_mm"])):
            if not math.isclose(actual, expected, abs_tol=.01):
                raise ValueError(f"Machining recipe {key} differs from order")
    stock_step, stock = _step_mesh(inputs["stock.step"])
    part_step, part = _step_mesh(inputs["machining.step"])
    # Independent triangulations differ slightly at curved STEP face seams.
    baseline_mismatch = max(0.0, (part - stock).volume())
    if (stock - part).volume() <= 0 or baseline_mismatch > part.volume() * .001:
        raise ValueError("Machining STEP must lie within stock STEP")
    z_shift = p.width / 2
    for shape in (stock_step, part_step):
        bbox = shape.BoundingBox()
        if not math.isclose(bbox.zmin, -p.width / 2, abs_tol=1) or not math.isclose(
                bbox.zmax, p.width / 2, abs_tol=1):
            raise ValueError("STEP Z bounds do not match the declared midplane datum")
    loops = window_loops(p)
    if len(loops) != report["windows"]:
        raise ValueError("Recipe window count differs from machining STEP report")
    axis, target = _window_grid(p, loops)
    segments, swept = raster_sweeps(target)
    if not segments:
        raise ValueError("No reachable 6 mm cutter raster in machining windows")
    z_bottom = min(z_back(p, r) for r in np.linspace(p.hub_r, p.ring_r, 40)) - 20 + z_shift
    z_upper = max(z_top(p, r) for r in np.linspace(p.hub_r, p.ring_r, 40)) + 20 + z_shift
    nc, layers = _reference_nc(p, axis, segments, z_upper, z_bottom, z_shift=z_shift)
    # The older reference generator's mesh-based turning contour is not an
    # exact section of stock.step. Omit it until a turning CAM uses that B-Rep.
    nc = "\n".join(line for line in nc.splitlines()
                   if "TURN CONTOUR REFERENCE" not in line and not line.startswith("(TURNING:")) + "\n"
    nc = ("(SOURCE stock.step + machining.step; Z0 RIM WIDTH MIDPLANE; +Z FACE; MM)\n"
          "(TURNING CONTOUR: USE STOCK.STEP IN FACTORY CAM; NO TURNING MOTION EMITTED)\n"
          "(WINDOW FOOTPRINT: MACHINING STEP BUILDER WINDOW LOOPS; SAMPLED RASTER)\n" + nc)
    section = _continuous_cutter(axis, segments) ^ m3.CrossSection.circle(through_radius(p), 512)
    cutter = section.extrude(z_upper - z_bottom).translate([0, 0, z_bottom])
    rough = stock - cutter
    if rough.status() != m3.Error.NoError:
        raise ValueError(f"STEP stock sampled Boolean failed: {rough.status()}")
    raw_gouge = max(0.0, (part - rough).volume())
    # Curved faces tessellated independently can cross by a few microns even
    # with no cutter. Compare the added intrusion with that unchanged baseline.
    gouge = max(0.0, raw_gouge - baseline_mismatch)
    residue = max(0.0, (rough - part).volume())
    # The source pair's full removed material also includes holes and seats.
    source_removed = max(0.0, (stock - part).volume())
    output.mkdir(parents=True, exist_ok=False)
    (output / "reference.nc").write_text(nc)
    with (output / "tool_list.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("tool", "operation", "type", "intent", "pending_review"))
        writer.writerows(_tool_rows(p))
    export_glb(stock, output / "stock_step.glb")
    export_glb(part, output / "machining_step.glb")
    export_glb(rough, output / "stock_after_roughing.glb")
    _render_3d_comparison(stock, rough, part, output / "simulation_3d.png", gouge,
                          center_z=0.0,
                          titles=("STOCK.STEP", "AFTER SAMPLED WINDOW SWEEP", "MACHINING.STEP"))
    plan = {
        "schema": "wheelcam-manufacturing-step-study-v1", "status": "not_released",
        "engineering_approved": False,
        "source": {name: {"path": str(path.resolve()), "sha256": _sha(path), "bytes": path.stat().st_size}
                   for name, path in inputs.items()},
        "coordinates": report["coordinates"],
        "basis": "Read back both exported STEP solids; window footprint from the same recipe and machining_step.window_loops builder.",
        "geometry": {"stock_step_valid": stock_step.isValid(), "machining_step_valid": part_step.isValid(),
                     "stock_mesh_volume_mm3": round(stock.volume(), 1),
                     "machining_mesh_volume_mm3": round(part.volume(), 1),
                     "stock_mesh_vs_step_volume_fraction": round(abs(stock.volume() - stock_step.Volume()) / stock_step.Volume(), 4),
                     "machining_mesh_vs_report_volume_fraction": round(abs(part.volume() - report["volume_mm3"]) / report["volume_mm3"], 4)},
        "operations": [
            {"id": "OP10", "type": "turning", "source": "stock.step", "status": "factory_cam_required"},
            {"id": "OP20", "type": "drilling", "hole_count": p.bolts, "hole_d_mm": p.bolt_d,
             "pcd_mm": p.pcd, "retract_plane_z_mm": round(face_z(p, p.pcd / 2) + z_shift + 5, 3),
             "drill_end_z_mm": round(z_back(p, p.hub_r) + z_shift - 3, 3), "status": "reference_only"},
            {"id": "OP30", "type": "conical_seat", "top_d_mm": p.seat_d,
             "included_angle_deg": p.seat_cone_deg, "status": "factory_cam_required"},
            {"id": "OP40", "type": "window_2_5d_roughing", "tool_d_mm": TOOL_D_MM,
             "step_over_mm": STEP_OVER_MM, "step_down_mm": STEP_DOWN_MM,
             "sampled_sweeps": len(segments), "depth_layers": layers, "status": "reference_only"},
        ],
        "simulation": {"type": "sampled_2_5d_mesh_boolean_from_step_pair", "grid_mm": GRID_MM,
                       "source_removed_mm3": round(source_removed, 1),
                       "sampled_overcut_area_mm2": round(float((swept & ~target).sum()) * GRID_MM ** 2, 1),
                       "step_pair_mesh_baseline_mismatch_mm3": round(baseline_mismatch, 1),
                       "raw_mesh_intrusion_mm3": round(raw_gouge, 1),
                       "gouge_vs_machining_step_mm3": round(gouge, 1),
                       "remaining_vs_machining_step_mm3": round(residue, 1),
                       "status": "sampled_no_gouge" if gouge < 1 else "requires_replanning",
                       "conclusion": (f"窗口粗加工相对加工级 STEP 未发现新增采样过切；余量 {residue / 1e6:.3f} L 留待工厂 CAM。"
                                      if gouge < 1 else
                                      f"窗口粗加工相对加工级 STEP 出现新增采样过切 {gouge / 1e6:.3f} L，需重新规划。")},
        "limits": ["STEP solids are tessellated for visualization and Boolean study; results are sampled, not exact CAM verification.",
                   "All NC motion is commented and cannot run on a controller.",
                   "No fixture, tool holder, machine collision, speeds, feeds, setup or postprocessor validation.",
                   "Machining STEP omits spoke face finishing; see source machining_report.json."],
    }
    (output / "process_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return plan
