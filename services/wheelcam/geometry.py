"""Deterministic concept geometry for template forged-monoblock-v5.

No image inference or manufacturing certification. All dimensions come from template.layout().
"""

import hashlib
import json
import math
import zipfile
from pathlib import Path

import cadquery as cq
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS

from .models import Preparation, WheelSpec
from .preparation import check_preparation, valve_geometry, write_handoff_files
from .template import HUB_EDGE_FILLET, LUG_SEAT_THICKNESS, TEMPLATE_VERSION, layout, round_polygon


def _volume(shape) -> float:
    # Shape.Volume() integrates coarsely: ~0.4 % off on the B-spline fillet surfaces.
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props, 1e-8, True)
    return props.Mass()


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
    return cq.Solid.makeLoft([_section_wire(frame) for frame in frames], ruled=False)


def _fuse_with_fillet(parts, radius, split_radius):
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
    attempts = [(radius, radius * f) for f in (1, 0.75, 0.5, 0.3, 0)] + [(radius / 2, 0)] if radius > 0 else []
    for hub_radius, rim_radius in attempts:
        fillet = BRepFilletAPI_MakeFillet(fused)
        for edge in hub_edges:
            fillet.Add(hub_radius, edge)
        for edge in rim_edges if rim_radius > 0 else ():
            fillet.Add(rim_radius, edge)
        try:
            fillet.Build()
        except Exception:  # OCCT raises StdFail_NotDone for fillets it cannot close
            continue
        if fillet.IsDone():
            candidate = cq.Shape.cast(fillet.Shape())
            if candidate.isValid() and len(candidate.Solids()) == 1:
                return candidate, {"hub": hub_radius, "rim": rim_radius}
    return cq.Shape.cast(fused), {"hub": 0.0, "rim": 0.0}


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
    hub = (cq.Workplane("XY", origin=(0, 0, spec.offset_et_mm))
           .circle(lay["hub_radius"]).extrude(spec.hub_thickness_mm)
           .edges(">Z").fillet(HUB_EDGE_FILLET).val())
    spoke = _loft(lay["sections"])
    if lay["paired_slot"]:
        slot = lay["paired_slot"]
        r, a, b = slot["radius_mm"], slot["start_r_mm"], slot["end_r_mm"]
        cutter = (cq.Workplane("XY", origin=(0, 0, -500)).moveTo(a + r, -r)
                  .lineTo(b, -r).lineTo(b, r).lineTo(a + r, r)
                  .threePointArc((a, 0), (a + r, -r)).close().extrude(1000).val())
        spoke = spoke.cut(cutter).clean()
    if lay["pockets"]:
        spoke = spoke.cut(_loft(lay["pockets"]))
    spokes = [spoke.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), spec.spoke_phase_deg + index * 360 / spec.spoke_count)
              for index in range(spec.spoke_count)]
    body, applied = _fuse_with_fillet([rim, hub, *spokes], spec.junction_fillet_mm,
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
                                      "rim_fillet_applied_mm": applied["rim"]}, rim


def build_wheel(spec: WheelSpec) -> tuple[cq.Workplane, dict]:
    wheel, info, _ = _build_wheel(spec)
    return wheel, info


def inspect_shape(shape, spec: WheelSpec) -> dict:
    solids = shape.Solids()
    bbox = shape.BoundingBox()
    volume = _volume(shape)
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
        "bbox_mm": [round(value, 4) for value in dimensions],
        "max_envelope_error_mm": round(max(errors), 8),
        "face_count": len(shape.Faces()),
        "derived": derived,
    }


def export_model(spec: WheelSpec, output: Path, preparation: Preparation | None = None, snapshot: dict | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    wheel, build_info, rim = _build_wheel(spec)
    report = inspect_shape(wheel.val(), spec)
    preparation = preparation or Preparation()
    snapshot = snapshot or {"spec": spec.model_dump(), "template_version": TEMPLATE_VERSION,
                            "preparation": preparation.model_dump()}
    if not (output / "recipe.json").exists():
        (output / "recipe.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    cq.exporters.export(wheel, str(output / "wheel.step"))
    # Reimport checks the serialized artifact, independently of the in-memory object.
    imported = cq.importers.importStep(str(output / "wheel.step")).val()
    reopened = inspect_shape(imported, spec)
    # STEP stores B-spline fillet surfaces at geometric tolerance; ~2e-6 relative was measured on the
    # default wheel, so 1e-5 still catches lost or damaged faces.
    relative_delta = abs(reopened["volume_mm3"] - report["volume_mm3"]) / report["volume_mm3"]
    if relative_delta > 1e-5:
        raise ValueError("STEP 导出回读后的体积不一致。")
    report["checks"]["step_roundtrip"] = True
    report["step_volume_relative_delta"] = relative_delta
    report["step_solid_count"] = reopened["solid_count"]
    from .appearance import export_previews
    report["presentation"] = export_previews(wheel, rim, spec, output)
    if spec.spoke_style == "paired":
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
    if spec.spoke_style == "paired":
        limitations.insert(0, "双辐为照片人工拟合的单片近似；分体连接、中心盖和周圈螺栓仅外观展示，不参与工程检查")
    if spec.lip_extension_mm:
        limitations.insert(0, "加宽轮唇和展开轮辐来自单张照片比例拟合；轮唇背部截面与厚度为假设，非实物尺寸恢复")
    report["preparation"] = check_preparation(wheel.val(), spec, preparation, output)
    report["handoff"] = write_handoff_files(output, spec, snapshot)
    report["model_id"] = snapshot.get("model_id")
    report["draft_revision"] = snapshot.get("draft_revision")
    report.update({
        "template_version": TEMPLATE_VERSION,
        "units": "mm", "coordinates": "右手系，轮毂轴线为 Z，轮辋宽度中面 Z=0，+Z 为外侧（装饰面）",
        "status": "geometry_checked", "engineering_approved": False,
        "limitations": limitations,
        "artifacts": {name: {"sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                              "bytes": (output / name).stat().st_size}
                      for name in ["wheel.step", "wheel.glb", "recipe.json", "features.json", "operations.csv",
                                   "stock.step", "caliper-envelope.step", "presentation.glb", "front.svg"] if (output / name).exists()},
    })
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output / "handoff.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in [*report["artifacts"], "report.json"]:
            archive.write(output / name, arcname=name)
    return report
