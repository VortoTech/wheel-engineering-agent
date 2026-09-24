import json
import math
import struct

import cadquery as cq
import pytest
from pydantic import ValidationError

from wheelcam.geometry import build_wheel, export_model, inspect_shape
from wheelcam.models import WheelSpec, default_sources, migrate_spec
from wheelcam.template import FLANGE_HEIGHT, INCH, LUG_SEAT_THICKNESS, layout, round_polygon

CASES = [
    {},
    {"spoke_count": 5, "rim_diameter_in": 20, "rim_width_in": 9.5, "offset_et_mm": 10, "sweep_deg": -20,
     "face_curve": 1.0, "spoke_width_hub_mm": 50, "spoke_width_rim_mm": 40},
    {"spoke_count": 10, "rim_diameter_in": 17, "rim_width_in": 7, "offset_et_mm": 45, "sweep_deg": 25,
     "spoke_width_hub_mm": 26, "spoke_width_rim_mm": 18, "spoke_thickness_mm": 20, "pocket_depth_mm": 0,
     "hub_thickness_mm": 32, "hub_diameter_mm": 160, "spoke_crown_mm": 0},
    {"rim_diameter_in": 22, "rim_width_in": 11, "offset_et_mm": 20, "spoke_count": 7, "spoke_thickness_mm": 40,
     "hub_thickness_mm": 60, "pocket_depth_mm": 24, "junction_fillet_mm": 8, "spoke_fillet_mm": 6,
     "spoke_crown_mm": 6, "hub_diameter_mm": 190},
]


@pytest.mark.parametrize("overrides", CASES)
def test_connected_solid_and_functional_features(overrides):
    spec = WheelSpec(**overrides)
    wheel, info = build_wheel(spec)
    shape = wheel.val()
    report = inspect_shape(shape, spec)
    assert all(report["checks"].values())
    assert info["junction_fillet_applied_mm"] == spec.junction_fillet_mm
    solid = shape.Solids()[0]
    lay = layout(spec)
    # Independent point probes verify actual material and voids, not just the input recipe.
    z = spec.offset_et_mm + 2  # behind the lug pockets, where only through holes exist
    assert not solid.isInside((0, 0, z))
    assert not solid.isInside((spec.center_bore_mm / 2 - 0.1, 0, z))
    assert solid.isInside((spec.center_bore_mm / 2 + 0.1, 0, z))
    pocket_z = spec.offset_et_mm + LUG_SEAT_THICKNESS + 2
    for index in range(spec.bolt_count):
        c, s = math.cos(2 * math.pi * index / spec.bolt_count), math.sin(2 * math.pi * index / spec.bolt_count)
        pcd = spec.bolt_circle_mm / 2
        assert not solid.isInside((pcd * c, pcd * s, z))
        beside_hole = pcd + spec.bolt_diameter_mm / 2 + 0.5
        assert solid.isInside((beside_hole * c, beside_hole * s, z))
        assert not solid.isInside((beside_hole * c, beside_hole * s, pocket_z))  # socket pocket above the seat

    R, half = spec.rim_diameter_in * INCH / 2, spec.rim_width_in * INCH / 2
    flange = lay["derived"]["flange_thickness_mm"]
    assert solid.isInside((R - 1, 0, half - 10))                     # under the bead seat
    assert not solid.isInside((R + 3, 0, half - 10))                 # tyre side of the bead seat
    assert solid.isInside((R + FLANGE_HEIGHT - 1, 0, half + flange / 2))  # flange lip
    well_r, well_z = lay["well_radius"], lay["well_mid_z"]
    assert solid.isInside((well_r - spec.rim_wall_mm / 2, 0, well_z))
    assert not solid.isInside((well_r + 2, 0, well_z))

    middle = lay["sections"][2]
    ox, oy = middle["origin"]
    assert solid.isInside((ox, oy, middle["front"] - 1))
    assert solid.isInside((ox, oy, middle["back"] + 1)) is (spec.pocket_depth_mm == 0)


@pytest.mark.parametrize("overrides", [
    {"center_bore_mm": 90, "bolt_circle_mm": 100},
    {"bolt_circle_mm": 140, "hub_diameter_mm": 140},
    {"spoke_count": 5.5}, {"spoke_count": True}, {"rim_diameter_in": 18.5}, {"rim_width_in": 8.3},
    {"offset_et_mm": float("nan")}, {"rim_wall_mm": -1},
    {"surprise_field": 1},
    {"offset_et_mm": 70, "hub_thickness_mm": 70},
    {"spoke_thickness_mm": 40, "hub_thickness_mm": 40},
    {"spoke_count": 10, "spoke_width_hub_mm": 60},
    {"spoke_width_hub_mm": 22, "spoke_width_rim_mm": 14, "pocket_depth_mm": 10},
])
def test_invalid_spec_rejected_before_kernel(overrides):
    with pytest.raises(ValidationError):
        WheelSpec(**overrides)


def test_rounded_polygon_keeps_every_side():
    segments = round_polygon([(0, 0), (10, 0), (10, 4), (0, 4)], [50, 50, 50, 50])
    lines = [segment for segment in segments if segment[0] == "line"]
    assert len(lines) == 4 and len(segments) == 8
    assert all(math.dist(a, b) > 0.01 for _, a, b in lines)


def test_legacy_spec_migrates_to_current_template():
    legacy = {"outer_diameter_mm": 480, "width_mm": 205, "rim_wall_mm": 9, "hub_diameter_mm": 160,
              "center_bore_mm": 66, "bolt_count": 5, "bolt_circle_mm": 114.3, "bolt_diameter_mm": 14,
              "spoke_count": 7, "spoke_width_mm": 38, "spoke_thickness_mm": 18, "dish_mm": 32, "sweep_deg": 10}
    sources = {key: {"kind": "measurement", "note": "卡尺"} for key in legacy}
    spec, new_sources = migrate_spec(legacy, sources)
    assert WheelSpec(**spec).spoke_count == 7 and spec["sweep_deg"] == 10
    assert new_sources["spoke_count"]["kind"] == "measurement"
    assert new_sources["offset_et_mm"]["kind"] == "template" and "模板升级" in new_sources["offset_et_mm"]["note"]
    assert migrate_spec(WheelSpec().model_dump(), default_sources()) is None


def test_export_artifacts_preserve_solid(tmp_path):
    spec = WheelSpec()
    report = export_model(spec, tmp_path)
    assert report["checks"]["step_roundtrip"]
    assert report["step_stability"]["fallback_used"] is False
    assert len(report["step_stability"]["attempts"]) == 1
    imported = cq.importers.importStep(str(tmp_path / "wheel.step")).val()
    assert len(imported.Solids()) == 1
    assert imported.BoundingBox().xlen == pytest.approx(18 * INCH + 2 * FLANGE_HEIGHT, abs=1e-4)
    data = (tmp_path / "wheel.glb").read_bytes()
    magic, version, length = struct.unpack("<III", data[:12])
    assert (magic, version, length) == (0x46546C67, 2, len(data))
    json_length, kind = struct.unpack("<II", data[12:20])
    gltf = json.loads(data[20:20 + json_length])
    assert gltf["meshes"]
    assert any(accessor.get("count", 0) > 100 for accessor in gltf["accessors"])
    assert report["engineering_approved"] is False
    assert report["derived"]["backspacing_mm"] > 0
    engineering = json.loads((tmp_path / "engineering.json").read_text())
    assert engineering["schema_version"] == "wheel-engineering-v1"
    assert engineering["manufacturing_status"] == "not_released"
    assert engineering["engineering_approved"] is False
    assert engineering["parameters"]["spoke_thickness_mm"]["source"] == "default"
    assert "spoke_thickness_mm" in engineering["critical_unconfirmed"]
    assert report["artifacts"]["engineering.json"]["sha256"]
