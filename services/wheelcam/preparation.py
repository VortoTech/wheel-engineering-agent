"""Deterministic preparation checks. No machine, tooling, or product approval is implied."""
import csv
import hashlib
import json
import math
from pathlib import Path

import cadquery as cq
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from .models import Preparation, StockSpec, WheelSpec
from .template import LUG_SEAT_THICKNESS, TEMPLATE_VERSION, layout

VOLUME_TOLERANCE_MM3 = 0.001
DISTANCE_TOLERANCE_MM = 0.00001


def volume(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props, 1e-8, True)
    return props.Mass()


def checked_volume(shape):
    if not shape.isValid():
        raise ValueError("加工准备布尔运算未得到有效几何，不能判定通过。")
    value = volume(shape)
    if not math.isfinite(value) or value < -VOLUME_TOLERANCE_MM3:
        raise ValueError("加工准备体积计算异常。")
    return max(0.0, value)


def checked_distance(first, second):
    value = first.distance(second)
    if not math.isfinite(value) or value < 0:
        raise ValueError("加工准备距离计算异常，不能判定通过。")
    return value


def stock_shape(stock: StockSpec):
    bottom = stock.center_z_mm - stock.height_mm / 2
    body = cq.Solid.makeCylinder(stock.outer_diameter_mm / 2, stock.height_mm, cq.Vector(0, 0, bottom))
    depth = stock.height_mm - stock.front_web_mm
    if stock.cavity_diameter_mm and depth > 0:
        cutter = cq.Solid.makeCylinder(stock.cavity_diameter_mm / 2, depth + 1, cq.Vector(0, 0, bottom - 1))
        body = body.cut(cutter)
    return body


def caliper_shape(spec: WheelSpec, caliper):
    bottom = spec.offset_et_mm + caliper.z_min_mm
    height = caliper.z_max_mm - caliper.z_min_mm
    return cq.Workplane("XY", origin=(0, 0, bottom)).circle(caliper.outer_radius_mm).circle(caliper.inner_radius_mm).extrude(height).val()


def check_preparation(wheel, spec: WheelSpec, prep: Preparation, output: Path | None = None):
    result = {"caliper": {"status": "not_checked", "reason": "未提供卡钳包络"},
              "stock": {"status": "not_checked", "reason": "未提供锻坯规格"},
              "weight": {"status": "not_checked", "reason": "未提供材料与密度"}}
    finished_volume = checked_volume(wheel)
    stock_volume = None
    if prep.caliper:
        c = prep.caliper
        envelope = caliper_shape(spec, c)
        overlap = checked_volume(wheel.intersect(envelope))
        distance = checked_distance(wheel, envelope) if overlap <= VOLUME_TOLERANCE_MM3 else 0.0
        status = "interference" if overlap > VOLUME_TOLERANCE_MM3 else (
            "insufficient_clearance" if distance <= DISTANCE_TOLERANCE_MM or distance + DISTANCE_TOLERANCE_MM < c.required_clearance_mm else "clear")
        result["caliper"] = {"status": status, "minimum_clearance_mm": round(distance, 5),
            "overlap_mm3": round(overlap, 6), "required_clearance_mm": c.required_clearance_mm,
            "method": "conservative_full_turn_annular_envelope", "input": c.model_dump(),
            "scope": "相对安装面的径向/轴向矩形绕 Z 轴旋转 360°；只检查此包络，未包含配重、气门嘴及变形"}
        if output:
            cq.exporters.export(envelope, str(output / "caliper-envelope.step"))
    if prep.stock:
        s = prep.stock
        blank = stock_shape(s)
        stock_volume = checked_volume(blank)
        missing = checked_volume(wheel.cut(blank))
        contained = missing <= VOLUME_TOLERANCE_MM3
        # Distances to boundary faces, not to the solid (which would be zero for containment).
        allowance = checked_distance(wheel, cq.Compound.makeCompound(blank.Faces())) if contained else None
        removed = max(0.0, stock_volume - finished_volume) if contained else None
        status = "missing_material" if not contained else (
            "insufficient_allowance" if allowance + DISTANCE_TOLERANCE_MM < s.required_allowance_mm else "contained")
        result["stock"] = {"status": status, "input": s.model_dump(),
            "stock_volume_mm3": round(stock_volume, 3), "finished_volume_mm3": round(finished_volume, 3),
            "missing_volume_mm3": round(missing, 6), "removed_volume_mm3": round(removed, 3) if removed is not None else None,
            "removal_percent": round(100 * removed / stock_volume, 3) if removed is not None else None,
            "minimum_allowance_mm": round(allowance, 5) if allowance is not None else None,
            "required_allowance_mm": s.required_allowance_mm,
            "scope": "同轴圆柱/杯形锻坯，内腔向 -Z 开口；所有边界均按待加工面计算统一最小余量"}
        if output:
            cq.exporters.export(blank, str(output / "stock.step"))
    if prep.material:
        material = prep.material
        kg_per_mm3 = material.density_kg_m3 / 1e9
        removed = result["stock"].get("removed_volume_mm3")
        result["weight"] = {"status": "estimated", "input": material.model_dump(),
            "finished_kg": round(finished_volume * kg_per_mm3, 4),
            "stock_kg": round(stock_volume * kg_per_mm3, 4) if stock_volume is not None else None,
            "removed_kg": round(removed * kg_per_mm3, 4) if removed is not None else None,
            "scope": "按同一种均匀密度计算的 CAD 净重；不含轮胎、紧固件、气门嘴、涂层及密度偏差"}
    return result


def valve_geometry(spec, lay):
    angle, tilt = math.radians(spec.valve_angle_deg), math.radians(spec.valve_tilt_deg)
    radius = lay["well_radius"] - spec.rim_wall_mm / 2
    center = [radius * math.cos(angle), radius * math.sin(angle), lay["well_mid_z"]]
    axis = [math.cos(tilt) * math.cos(angle), math.cos(tilt) * math.sin(angle), math.sin(tilt)]
    length = spec.rim_wall_mm / math.cos(tilt) + 12
    return center, axis, length


def feature_manifest(spec: WheelSpec, snapshot, step_sha256):
    lay = layout(spec)
    features = []

    def add(id, kind, name, parameters, setup, operation):
        features.append({"id": id, "kind": kind, "name": name, "parameters": parameters,
                         "setup_candidate": setup, "operation_candidate": operation,
                         "status": "draft", "selection": "semantic_region_requires_cam_face_confirmation"})

    rim_profile = [{"r_mm": p[0], "z_mm": p[1], "corner_radius_mm": r} for p, r in lay["rim_polygon"]]
    add("rim-profile", "revolved_profile", "轮辋回转轮廓", {"axis": [0, 0, 1], "profile_rz": rim_profile}, "正/背面装夹待分配", "粗车 / 精车")
    add("mount-face", "plane", "安装面", {"z_mm": spec.offset_et_mm, "normal": [0, 0, -1], "outer_diameter_mm": spec.hub_diameter_mm}, "背面", "端面车削")
    add("center-bore", "bore", "中心孔", {"diameter_mm": spec.center_bore_mm, "origin_mm": [0, 0, spec.offset_et_mm], "axis": [0, 0, 1], "depth_mm": spec.hub_thickness_mm}, "背面", "镗孔 / 精加工")
    for index in range(spec.bolt_count):
        theta = 2 * math.pi * index / spec.bolt_count
        add(f"bolt-{index + 1:02}", "stepped_hole", f"安装孔 {index + 1}", {
            "origin_mm": [spec.bolt_circle_mm / 2 * math.cos(theta), spec.bolt_circle_mm / 2 * math.sin(theta), spec.offset_et_mm],
            "axis": [0, 0, 1], "diameter_mm": spec.bolt_diameter_mm, "depth_mm": spec.hub_thickness_mm,
            "seat_included_angle_deg": 60, "seat_top_diameter_mm": lay["lug_cone_diameter"],
            "seat_top_z_mm": spec.offset_et_mm + LUG_SEAT_THICKNESS, "socket_diameter_mm": lay["lug_pocket_diameter"]}, "正面", "钻孔 / 锥面座 / 沉孔")
    for index in range(spec.spoke_count):
        angle = index * 360 / spec.spoke_count
        descriptor = {"rotation_deg": angle, "rotation_axis": [0, 0, 1],
                      "section_frames": lay["sections"]}
        add(f"spoke-{index + 1:02}", "loft_surface", f"轮辐曲面 {index + 1}", descriptor, "正面", "曲面粗铣 / 精铣")
        add(f"window-{index + 1:02}", "interspoke_region", f"轮辐窗口 {index + 1}", {
            "between_features": [f"spoke-{index + 1:02}", f"spoke-{(index + 1) % spec.spoke_count + 1:02}"],
            "inner_radius_mm": lay["hub_radius"], "outer_radius_mm": lay["well_radius"],
            "boundary_note": "以相邻轮辐实体、中心盘和轮辋为边界；窗口不是独立孔或固定角度扇区"}, "正面/分度待确认", "窗口开粗 / 侧壁精铣")
        if lay["pockets"]:
            add(f"pocket-{index + 1:02}", "back_pocket", f"背腔 {index + 1}", {
                "rotation_deg": angle, "section_frames": lay["pockets"], "floor_radius_mm": 3}, "背面", "背腔粗铣 / 精铣")
    if spec.valve_diameter_mm:
        center, axis, length = valve_geometry(spec, lay)
        add("valve-01", "angled_hole", "气门孔", {"center_mm": center, "axis": axis,
            "diameter_mm": spec.valve_diameter_mm, "cutter_span_mm": length,
            "note": "刀具体覆盖长度不是钻削深度；密封座与气门嘴规格待确认"}, "分度/倾斜装夹待确认", "钻气门孔 / 去毛刺")
    operations = []
    grouped = {}
    for feature in features:
        key = feature["setup_candidate"], feature["operation_candidate"]
        grouped.setdefault(key, []).append(feature["id"])
    for index, ((setup, operation), ids) in enumerate(grouped.items(), 1):
        operations.append({"id": f"op-{index:02}", "setup_candidate": setup,
            "operation_candidate": operation, "feature_ids": ids, "status": "draft",
            "pending": "加工顺序、机床轴数、工件坐标、夹具、刀具、切削参数、公差与粗糙度均待确认"})
    return {"schema_version": 1, "template_version": TEMPLATE_VERSION, "status": "draft",
        "model_id": snapshot.get("model_id"), "draft_revision": snapshot.get("draft_revision"),
        "step_sha256": step_sha256, "units": "mm", "coordinates": "Z 为轮毂轴线，宽度中面 Z=0，+Z 外侧；安装面 Z=ET",
        "spec": spec.model_dump(), "sources": snapshot.get("sources", {}),
        "preparation": snapshot.get("preparation", {}), "features": features, "operations": operations,
        "limitations": ["特征按模板语义定位，不包含可直接绑定的 STEP 面编号；CAM 选面需复核",
                        "工序按类型分组，编号不代表已验证的加工顺序；无刀路、后处理或 NC"]}


def write_handoff_files(output: Path, spec, snapshot):
    step_hash = hashlib.sha256((output / "wheel.step").read_bytes()).hexdigest()
    manifest = feature_manifest(spec, snapshot, step_hash)
    (output / "features.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with (output / "operations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["模型版本", "草稿版本", "STEP SHA256", "工序编号", "候选装夹", "候选工序", "特征标识", "状态", "待确认"])
        for op in manifest["operations"]:
            writer.writerow([manifest["model_id"], manifest["draft_revision"], step_hash, op["id"],
                op["setup_candidate"], op["operation_candidate"], " / ".join(op["feature_ids"]), "草案", op["pending"]])
    return {"status": "draft", "feature_count": len(manifest["features"]), "operation_count": len(manifest["operations"])}
