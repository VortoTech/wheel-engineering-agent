"""Engineering intent schema between visual evidence and parametric CAD.

This module deliberately permits UNKNOWN values.  A concrete WheelSpec still
contains defaults so it can build a concept solid, while this companion record
states which values are evidence, supplied requirements, inferences, defaults,
or unresolved.  It is the release boundary; a valid STEP is not approval.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .models import WheelSpec
from .template import TEMPLATE_VERSION
from .build_resolution import BuildResolution


ParameterOrigin = Literal["observed", "specified", "inferred", "default", "unknown"]


class EngineeringParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: JsonValue | None
    unit: str
    source: ParameterOrigin
    confidence: float = Field(ge=0, le=1)
    minimum: float | int | None = None
    maximum: float | int | None = None
    constraints: list[str] = Field(default_factory=list)
    critical: bool = False
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def unknown_is_explicit(self):
        if self.source == "unknown" and (self.value is not None or self.confidence != 0):
            raise ValueError("UNKNOWN 参数必须为空且置信度为 0。")
        if self.source != "unknown" and self.value is None:
            raise ValueError("非 UNKNOWN 参数必须有值。")
        return self


class FeatureIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    operation: Literal["revolve", "extrude", "loft", "sweep", "cut", "circular_pattern", "fillet", "boolean_union"]
    parameter_keys: list[str]
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["parametric", "conditional", "unresolved"] = "parametric"
    note: str = ""


class WheelEngineeringDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wheel-engineering-v1"] = "wheel-engineering-v1"
    template_version: str = TEMPLATE_VERSION
    family: Literal["forged_monoblock"] = "forged_monoblock"
    units: Literal["mm"] = "mm"
    coordinate_system: str = "右手系；Z 为轮毂轴线，+Z 为装饰面外侧"
    parameters: dict[str, EngineeringParameter]
    build_resolution: BuildResolution | None = None
    feature_tree: list[FeatureIntent]
    critical_unknowns: list[str]
    critical_unconfirmed: list[str]
    manufacturing_status: Literal["not_released"] = "not_released"
    engineering_approved: Literal[False] = False
    limitations: list[str]


CRITICAL_PARAMETERS = {
    "rim_diameter_in", "rim_width_in", "offset_et_mm", "rim_wall_mm",
    "hub_thickness_mm", "center_bore_mm", "bolt_count", "bolt_circle_mm",
    "bolt_diameter_mm", "spoke_count", "spoke_thickness_mm",
}

SOURCE_TRANSLATION = {
    "template": ("default", .35),
    "manual": ("specified", .9),
    "drawing": ("specified", .98),
    "measurement": ("specified", 1.0),
}


def _unit(name):
    if name.endswith("_in"):
        return "in"
    if name.endswith("_mm") or name == "window_outlines_mm":
        return "mm"
    if name.endswith("_deg"):
        return "deg"
    if name.endswith("_count") or name in {"bolt_count", "spoke_count"}:
        return "count"
    return "dimensionless"


def _field_limits(name):
    schema = WheelSpec.model_json_schema()["properties"][name]
    constraints = []
    if "multipleOf" in schema:
        constraints.append(f"multiple_of={schema['multipleOf']}")
    if "enum" in schema:
        constraints.append("enum="+"|".join(map(str, schema["enum"])))
    return schema.get("minimum"), schema.get("maximum"), constraints


def _feature_tree():
    return [
        FeatureIntent(id="rim", operation="revolve", parameter_keys=[
            "rim_diameter_in", "rim_width_in", "rim_wall_mm", "lip_extension_mm", "lip_drop_mm"]),
        FeatureIntent(id="hub", operation="extrude", parameter_keys=[
            "hub_diameter_mm", "hub_thickness_mm", "offset_et_mm"]),
        FeatureIntent(id="center_bore", operation="cut", parameter_keys=["center_bore_mm"], depends_on=["hub"]),
        FeatureIntent(id="bolt_master", operation="cut", parameter_keys=[
            "bolt_circle_mm", "bolt_diameter_mm"], depends_on=["hub"]),
        FeatureIntent(id="bolt_pattern", operation="circular_pattern", parameter_keys=["bolt_count"],
                      depends_on=["bolt_master"]),
        FeatureIntent(id="spoke_master", operation="loft", parameter_keys=[
            "spoke_style", "spoke_method", "spoke_width_hub_mm", "spoke_width_rim_mm",
            "spoke_thickness_mm", "spoke_crown_mm", "face_curve", "sweep_deg"],
            depends_on=["hub", "rim"], status="conditional",
            note="loft 或 window 方法由显式参数选择；不是由图片网格替代。"),
        FeatureIntent(id="spoke_pattern", operation="circular_pattern", parameter_keys=[
            "spoke_count", "spoke_phase_deg"], depends_on=["spoke_master"]),
        FeatureIntent(id="wheel_union", operation="boolean_union", parameter_keys=[],
                      depends_on=["rim", "hub", "spoke_pattern"]),
        FeatureIntent(id="junction_fillets", operation="fillet", parameter_keys=[
            "junction_fillet_mm", "spoke_fillet_mm"], depends_on=["wheel_union"], status="conditional"),
        FeatureIntent(id="rim_pockets", operation="circular_pattern", parameter_keys=[
            "rim_pocket_count", "rim_pocket_phase_deg", "rim_pocket_radial_mm", "rim_pocket_width_mm",
            "rim_pocket_depth_mm", "rim_pocket_inset_mm", "rim_pocket_corner_mm"],
            depends_on=["rim"], status="conditional"),
        FeatureIntent(id="valve", operation="cut", parameter_keys=[
            "valve_diameter_mm", "valve_angle_deg", "valve_tilt_deg"],
            depends_on=["rim"], status="conditional"),
    ]


def build_engineering_definition(spec: WheelSpec, sources=None, unknown_fields=(), *, build_resolution=None):
    sources = sources or {}
    unknown_fields = set(unknown_fields)
    invalid = unknown_fields-set(WheelSpec.model_fields)
    if invalid:
        raise ValueError("未知参数名："+", ".join(sorted(invalid)))
    parameters = {}
    for name, value in spec.model_dump(mode="json").items():
        minimum, maximum, constraints = _field_limits(name)
        raw = sources.get(name, {})
        raw_kind = raw.get("kind", "template") if isinstance(raw, dict) else getattr(raw, "kind", "template")
        note = raw.get("note", "") if isinstance(raw, dict) else getattr(raw, "note", "")
        if name in unknown_fields:
            origin, confidence, value, note = "unknown", 0, None, note or "尚未提供或无法由当前输入观测"
        elif raw_kind in SOURCE_TRANSLATION:
            origin, confidence = SOURCE_TRANSLATION[raw_kind]
            supplied_confidence = raw.get("confidence") if isinstance(raw, dict) else getattr(raw, "confidence", None)
            if supplied_confidence is not None:
                confidence = float(supplied_confidence)
        elif raw_kind in {"observed", "specified", "inferred", "default", "unknown"}:
            origin = raw_kind
            supplied_confidence = raw.get("confidence") if isinstance(raw, dict) else getattr(raw, "confidence", None)
            confidence = float(supplied_confidence if supplied_confidence is not None else
                               (0 if origin == "unknown" else .5))
            if origin == "unknown":
                value = None
        else:
            raise ValueError(f"参数 {name} 的来源 {raw_kind!r} 不受支持。")
        parameters[name] = EngineeringParameter(
            value=value, unit=_unit(name), source=origin, confidence=confidence,
            minimum=minimum, maximum=maximum, constraints=constraints,
            critical=name in CRITICAL_PARAMETERS, note=note,
        )
    critical_unknowns = sorted(name for name, item in parameters.items() if item.critical and item.source == "unknown")
    critical_unconfirmed = sorted(name for name, item in parameters.items()
                                  if item.critical and item.source in {"default", "inferred"})
    return WheelEngineeringDefinition(
        parameters=parameters, feature_tree=_feature_tree(), build_resolution=build_resolution,
        critical_unknowns=critical_unknowns, critical_unconfirmed=critical_unconfirmed,
        limitations=[
            "合法 B-Rep/STEP 只证明几何构造与序列化有效，不证明尺寸、强度、疲劳或法规符合性。",
            "照片观测不得自动覆盖用户规格或实测尺寸；不可观测的背面、厚度和安装结构允许保持 UNKNOWN。",
            "manufacturing_status 固定为 not_released，直到关键参数确认和独立工程验证完成。",
            "parameters 是输入证据；实际构造差异见 build_resolution，不能将 UNKNOWN 的构建假设当成实测值。",
            "feature_tree 当前为描述性摘要，不是可执行的 CAD 特征图。",
        ],
    )
