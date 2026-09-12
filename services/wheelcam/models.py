from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TEMPLATE_VERSION = "radial-loft-concept-v1"


class WheelSpec(BaseModel):
    """Physical dimensions in millimetres, not a standardized tyre rim profile."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    outer_diameter_mm: float = Field(480, ge=380, le=600)
    width_mm: float = Field(205, ge=150, le=280)
    rim_wall_mm: float = Field(9, ge=6, le=16)
    hub_diameter_mm: float = Field(160, ge=140, le=190)
    center_bore_mm: float = Field(66, ge=45, le=90)
    bolt_count: int = Field(5, ge=4, le=6, strict=True)
    bolt_circle_mm: float = Field(114.3, ge=100, le=140)
    bolt_diameter_mm: float = Field(14, ge=10, le=18)
    spoke_count: int = Field(6, ge=5, le=10, strict=True)
    spoke_width_mm: float = Field(38, ge=24, le=52)
    spoke_thickness_mm: float = Field(18, ge=12, le=26)
    dish_mm: float = Field(32, ge=12, le=55)
    sweep_deg: float = Field(10, ge=-18, le=18)

    @model_validator(mode="after")
    def check_clearances(self):
        if self.bolt_circle_mm + self.bolt_diameter_mm + 16 > self.hub_diameter_mm:
            raise ValueError("安装孔距离中心盘外缘过近，请增加中心盘直径或减小孔距。")
        if self.center_bore_mm + self.bolt_diameter_mm + 12 > self.bolt_circle_mm:
            raise ValueError("中心孔与安装孔之间的间隔不足。")
        return self


class ParameterSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["template", "manual", "drawing", "measurement"] = "template"
    note: str = Field("概念模板默认值，未作工程确认", max_length=200)


def default_sources():
    return {key: ParameterSource().model_dump() for key in WheelSpec.model_fields}


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
