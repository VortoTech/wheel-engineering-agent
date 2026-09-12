"""Deterministic concept geometry. No image inference or manufacturing certification."""

import hashlib
import json
import math
from pathlib import Path

import cadquery as cq

from .models import TEMPLATE_VERSION, WheelSpec


def build_wheel(spec: WheelSpec) -> cq.Workplane:
    r = spec.outer_diameter_mm / 2
    half = spec.width_mm / 2
    wall = spec.rim_wall_mm
    # Simplified barrel with front/back lips. This is not a standardized bead seat.
    profile = [
        (r, -half), (r, -half + 8), (r - 3, -half + 8),
        (r - 3, half - 8), (r, half - 8), (r, half),
        (r - wall, half), (r - wall, -half),
    ]
    rim = cq.Workplane("XZ").polyline(profile).close().revolve(360, (0, 0), (0, 1))
    outer_z = half - 14 - spec.spoke_thickness_mm / 2
    hub_z = outer_z - spec.dish_mm
    hub_thickness = spec.spoke_thickness_mm + 10
    hub = (
        cq.Workplane("XY", origin=(0, 0, hub_z - hub_thickness / 2))
        .circle(spec.hub_diameter_mm / 2).circle(spec.center_bore_mm / 2)
        .extrude(hub_thickness)
    )
    wires = []
    for t, radius in [(0, spec.hub_diameter_mm / 2 - 12),
                      (0.5, (spec.hub_diameter_mm / 2 + r - wall) / 2),
                      (1, r - wall / 2 - 1)]:
        angle = math.radians(spec.sweep_deg * t)
        z = hub_z + spec.dish_mm * t
        radial = (math.cos(angle), math.sin(angle), 0)
        tangent = (-math.sin(angle), math.cos(angle), 0)
        plane = cq.Plane(origin=(radius * radial[0], radius * radial[1], z),
                         xDir=tangent, normal=radial)
        wire = cq.Workplane(plane).rect(spec.spoke_width_mm * (0.85 + 0.3 * t),
                                        spec.spoke_thickness_mm).val()
        wires.append(wire)
    spoke = cq.Solid.makeLoft(wires, ruled=False)
    result = rim.union(hub)
    for index in range(spec.spoke_count):
        result = result.union(spoke.rotate((0, 0, 0), (0, 0, 1), index * 360 / spec.spoke_count))
    holes = []
    for index in range(spec.bolt_count):
        angle = 2 * math.pi * index / spec.bolt_count
        holes.append((spec.bolt_circle_mm / 2 * math.cos(angle),
                      spec.bolt_circle_mm / 2 * math.sin(angle)))
    cutters = cq.Workplane("XY", origin=(0, 0, -half - 1)).pushPoints(holes).circle(
        spec.bolt_diameter_mm / 2).extrude(spec.width_mm + 2)
    return result.cut(cutters).clean()


def inspect_shape(shape, spec: WheelSpec) -> dict:
    solids = shape.Solids()
    bbox = shape.BoundingBox()
    volume = shape.Volume()
    dimensions = [bbox.xlen, bbox.ylen, bbox.zlen]
    expected = [spec.outer_diameter_mm, spec.outer_diameter_mm, spec.width_mm]
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
        "bbox_mm": [round(value, 4) for value in dimensions],
        "max_envelope_error_mm": round(max(errors), 8),
        "face_count": len(shape.Faces()),
    }


def export_model(spec: WheelSpec, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    wheel = build_wheel(spec)
    report = inspect_shape(wheel.val(), spec)
    cq.exporters.export(wheel, str(output / "wheel.step"))
    # Reimport checks the serialized artifact, independently of the in-memory object.
    imported = cq.importers.importStep(str(output / "wheel.step")).val()
    reopened = inspect_shape(imported, spec)
    relative_delta = abs(imported.Volume() - wheel.val().Volume()) / wheel.val().Volume()
    if relative_delta > 1e-7:
        raise ValueError("STEP 导出回读后的体积不一致。")
    report["checks"]["step_roundtrip"] = True
    report["step_volume_relative_delta"] = relative_delta
    report["step_solid_count"] = reopened["solid_count"]
    assembly = cq.Assembly(wheel, name="wheel-concept", color=cq.Color(0.63, 0.67, 0.73))
    assembly.export(str(output / "wheel.glb"), tolerance=0.15, angularTolerance=0.1)
    report.update({
        "template_version": TEMPLATE_VERSION,
        "units": "mm", "coordinates": "右手系，轮毂轴线为 Z，宽度中面 Z=0",
        "status": "geometry_checked", "engineering_approved": False,
        "limitations": ["概念模板；轮辋截面未按轮胎配合规格设计", "轮辐过渡圆角与紧固件座面待设计",
                        "材料、公差、载荷及毛坯尚未确认", "未进行结构分析、CAM 或机床验证"],
        "artifacts": {name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                              "bytes": (output / name).stat().st_size}
                      for name in ["wheel.step", "wheel.glb"]},
    })
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
