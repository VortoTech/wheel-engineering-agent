from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .template import TEMPLATE_VERSION, layout

__all__ = ["TEMPLATE_VERSION", "WheelSpec", "ParameterSource", "default_sources", "migrate_spec",
           "ProjectCreate", "DraftUpdate", "BuildRequest"]


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
    spoke_width_hub_mm: float = Field(38, ge=22, le=60)
    spoke_width_rim_mm: float = Field(30, ge=14, le=50)
    spoke_thickness_mm: float = Field(28, ge=16, le=40)
    spoke_crown_mm: float = Field(2, ge=0, le=6)
    spoke_fillet_mm: float = Field(3, ge=1, le=6)
    face_curve: float = Field(0.5, ge=0, le=1)
    sweep_deg: float = Field(8, ge=-25, le=25)
    pocket_depth_mm: float = Field(12, ge=0, le=24)
    junction_fillet_mm: float = Field(5, ge=0, le=8)

    @model_validator(mode="after")
    def check_layout(self):
        layout(self)
        return self


class ParameterSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["template", "manual", "drawing", "measurement"] = "template"
    note: str = Field("概念模板默认值，未作工程确认", max_length=200)


def default_sources():
    return {key: ParameterSource().model_dump() for key in WheelSpec.model_fields}


def migrate_spec(spec: dict, sources: dict):
    """Carry a draft from an older template onto WheelSpec. Returns None if already current.

    Fields that still exist keep their value and source when the combined spec stays valid;
    everything else takes the template default and is marked as such.
    """
    if set(spec) == set(WheelSpec.model_fields):
        return None
    merged = WheelSpec().model_dump()
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


class DraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    spec: WheelSpec
    sources: dict[str, ParameterSource]
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def source_keys(self):
        if set(self.sources) != set(WheelSpec.model_fields):
            raise ValueError("每个参数必须保留来源。")
        return self


class BuildRequest(BaseModel):
    expected_revision: int = Field(ge=1)
