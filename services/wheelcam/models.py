from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .template import TEMPLATE_VERSION, layout

__all__ = ["TEMPLATE_VERSION", "WheelSpec", "ParameterSource", "default_sources", "migrate_spec",
           "ProjectCreate", "DraftUpdate", "BuildRequest", "Preparation", "StockSpec", "CaliperSpec", "MaterialSpec"]


class WheelSpec(BaseModel):
    """Forged monoblock template. Millimetres unless the field name says otherwise.

    Rim size follows the "18 x 8.5J" convention: bead seat diameter and flange-to-flange width in inches.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    rim_diameter_in: int = Field(18, ge=17, le=22, strict=True)
    rim_width_in: float = Field(8.5, ge=7, le=11, multiple_of=0.5)
    offset_et_mm: float = Field(35, ge=-20, le=70)
    rim_wall_mm: float = Field(5.5, ge=4.5, le=10)
    hub_diameter_mm: float = Field(165, ge=140, le=200)
    hub_thickness_mm: float = Field(48, ge=30, le=70)
    center_bore_mm: float = Field(66, ge=50, le=90)
    bolt_count: int = Field(5, ge=4, le=6, strict=True)
    bolt_circle_mm: float = Field(114.3, ge=98, le=140)
    bolt_diameter_mm: float = Field(14, ge=12, le=16)
    spoke_count: int = Field(6, ge=5, le=10, strict=True)
    spoke_style: Literal["single", "paired"] = "single"
    paired_blade_root_mm: float = Field(0, ge=0, le=20)
    paired_window_root_mm: float = Field(0, ge=0, le=35)
    paired_window_blend_mm: float = Field(55, ge=25, le=85)
    paired_root_round_mm: float = Field(0, ge=0, le=25)
    paired_gap_flare_mm: float = Field(0, ge=0, le=16)
    paired_gap_mm: float = Field(34, ge=16, le=50)
    paired_tip_width_mm: float = Field(6, ge=4, le=14)
    paired_split_start_mm: float = Field(8, ge=0, le=25)
    paired_shoulder_mm: float = Field(0, ge=0, le=48)
    paired_mid_mm: float = Field(0, ge=-6, le=32)
    paired_tip_inset_mm: float = Field(0, ge=0, le=22)
    lip_extension_mm: float = Field(0, ge=0, le=48)
    lip_drop_mm: float = Field(22, ge=10, le=32)
    spoke_phase_deg: float = Field(0, ge=0, lt=360)
    spoke_width_hub_mm: float = Field(38, ge=22, le=60)
    spoke_width_rim_mm: float = Field(30, ge=14, le=50)
    spoke_thickness_mm: float = Field(28, ge=16, le=40)
    spoke_crown_mm: float = Field(2, ge=0, le=6)
    spoke_fillet_mm: float = Field(3, ge=0.5, le=6)
    face_curve: float = Field(0.5, ge=0, le=1)
    sweep_deg: float = Field(8, ge=-25, le=25)
    pocket_depth_mm: float = Field(12, ge=0, le=24)
    junction_fillet_mm: float = Field(5, ge=0, le=8)
    valve_diameter_mm: float = Field(0, ge=0, le=16)
    valve_angle_deg: float = Field(30, ge=0, lt=360)
    valve_tilt_deg: float = Field(0, ge=-25, le=25)
    # "loft": v9 section-lofted spokes. "window": a turned blank minus window outlines (v10).
    spoke_method: Literal["loft", "window"] = "loft"
    # Window outlines, XY mm in the group-0 frame; repeated spoke_count times from spoke_phase_deg.
    window_outlines_mm: list[Annotated[list[tuple[float, float]], Field(min_length=8, max_length=400)]] = Field(
        default_factory=list, max_length=12)
    window_edge_fillet_mm: float = Field(2, ge=0, le=5)
    # Shallow front-face shoulder derived from every fitted window outline; 0 preserves old models.
    window_face_relief_mm: float = Field(0, ge=0, le=3)
    # Raised paired-spoke centre bands derived from the solid webs between three fitted windows.
    window_spoke_ridge_mm: float = Field(0, ge=0, le=3)
    # Back opening contracts from the photographed front outline; a single-photo design assumption.
    window_side_draft_deg: float = Field(0, ge=0, le=10)
    # Independent front-lip pocket array. A zero count keeps legacy geometry unchanged.
    rim_pocket_count: int = Field(0, ge=0, le=40, strict=True)
    rim_pocket_phase_deg: float = Field(0, ge=0, lt=360)
    rim_pocket_radial_mm: float = Field(30, ge=10, le=48)
    rim_pocket_width_mm: float = Field(14, ge=6, le=28)
    rim_pocket_depth_mm: float = Field(7, ge=2, le=14)
    rim_pocket_inset_mm: float = Field(7, ge=3, le=20)
    rim_pocket_corner_mm: float = Field(3, ge=0.5, le=8)

    @model_validator(mode="after")
    def check_layout(self):
        if 0 < self.valve_diameter_mm < 5:
            raise ValueError("气门孔径须为 0（关闭）或 5–16 mm。")
        layout(self)
        return self


class ParameterSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["template", "manual", "drawing", "measurement", "observed", "inferred", "unknown"] = "template"
    confidence: float | None = Field(None, ge=0, le=1)
    note: str = Field("概念模板默认值，未作工程确认", max_length=200)


def default_sources():
    return {key: ParameterSource().model_dump() for key in WheelSpec.model_fields}


class EngineeringInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source: ParameterSource = Field(default_factory=ParameterSource)


class CaliperSpec(EngineeringInput):
    """Conservative full-turn annular envelope; z is relative to the mounting face."""
    inner_radius_mm: float = Field(90, ge=1, le=400)
    outer_radius_mm: float = Field(180, ge=2, le=450)
    z_min_mm: float = Field(-70, ge=-500, le=500)
    z_max_mm: float = Field(10, ge=-500, le=500)
    required_clearance_mm: float = Field(3, ge=0, le=30)

    @model_validator(mode="after")
    def ordered(self):
        if self.inner_radius_mm >= self.outer_radius_mm or self.z_min_mm >= self.z_max_mm:
            raise ValueError("卡钳包络须满足内半径 < 外半径、轴向起点 < 终点。")
        return self


class StockSpec(EngineeringInput):
    """Cylinder or cup, cavity opens toward -Z. All coordinates use wheel mid-plane."""
    outer_diameter_mm: float = Field(520, ge=100, le=1000)
    height_mm: float = Field(280, ge=20, le=600)
    center_z_mm: float = Field(0, ge=-500, le=500)
    cavity_diameter_mm: float = Field(390, ge=0, le=990)
    front_web_mm: float = Field(120, ge=1, le=600)
    required_allowance_mm: float = Field(1, ge=0, le=20)

    @model_validator(mode="after")
    def material(self):
        a = self.required_allowance_mm
        if self.front_web_mm > self.height_mm or self.height_mm <= 2 * a:
            raise ValueError("锻坯底厚不得超过总高，总高须大于两倍余量。")
        if self.cavity_diameter_mm >= self.outer_diameter_mm:
            raise ValueError("锻坯内腔直径须小于外径。")
        if self.outer_diameter_mm <= 2 * a or (self.cavity_diameter_mm > 0 and
                (self.outer_diameter_mm - self.cavity_diameter_mm <= 4 * a or self.front_web_mm <= 2 * a)):
            raise ValueError("锻坯壁厚或底厚不足以容纳所要求的余量。")
        return self


class MaterialSpec(EngineeringInput):
    name: str = Field(min_length=1, max_length=100)
    density_kg_m3: float = Field(gt=0, le=30000)


class Preparation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caliper: CaliperSpec | None = None
    stock: StockSpec | None = None
    material: MaterialSpec | None = None


def migrate_spec(spec: dict, sources: dict):
    """Carry a draft from an older template onto WheelSpec. Returns None if already current.

    Fields that still exist keep their value and source when the combined spec stays valid;
    everything else takes the template default and is marked as such.
    """
    if set(spec) == set(WheelSpec.model_fields):
        return None
    merged = WheelSpec().model_dump()
    # Preserve valid combinations atomically before attempting legacy field recovery.
    candidate = {**merged, **{k: v for k, v in spec.items() if k in merged}}
    try:
        WheelSpec(**candidate)
    except ValidationError:
        pass
    else:
        upgraded = ParameterSource(note=f"模板升级为 {TEMPLATE_VERSION}，采用新模板默认值").model_dump()
        return candidate, {k: sources[k] if k in spec and k in sources else upgraded for k in candidate}
    kept = set()
    for key in merged:
        if key not in spec:
            continue
        try:
            WheelSpec(**{**merged, key: spec[key]})
        except ValidationError:
            continue
        merged[key] = spec[key]
        kept.add(key)
    upgraded = ParameterSource(note=f"模板升级为 {TEMPLATE_VERSION}，采用新模板默认值").model_dump()
    new_sources = {key: sources[key] if key in kept and key in sources else upgraded for key in merged}
    return merged, new_sources


class ProjectCreate(BaseModel):
    name: str = Field("轮毂概念 01", min_length=1, max_length=80)
    preset: Literal["single", "photo-paired-8", "photo-paired-refined"] = "single"


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    spec: WheelSpec
    sources: dict[str, ParameterSource]
    preparation: Preparation = Field(default_factory=Preparation)
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def source_keys(self):
        if set(self.sources) != set(WheelSpec.model_fields):
            raise ValueError("每个参数必须保留来源。")
        return self


class BuildRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class ForgedBuildRequest(BuildRequest):
    recipe: dict = Field(default_factory=dict)


class ForgedPhotoFitRequest(BaseModel):
    """Clicks in original-image pixels: rim edge points, hub centre, one group's window polygons."""
    rim_points: list[tuple[float, float]] = Field(min_length=5, max_length=200)
    hub_point: tuple[float, float]
    windows: list[list[tuple[float, float]]] = Field(min_length=1, max_length=6)
    groups: int = Field(ge=3, le=12)
    bolts: int | None = Field(default=None, ge=3, le=10)
    base_recipe: dict = Field(default_factory=dict)
    image_id: str | None = None


class AnalysisRequest(BuildRequest):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    reference_outer_mm: float | None = Field(None, ge=100, le=1200)


class RootCorrectionRequest(BuildRequest):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    group: int = Field(ge=0, le=9, strict=True)
    points: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
