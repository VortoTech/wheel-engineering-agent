import json
import math
import struct

import cadquery as cq
import pytest
from pydantic import ValidationError

from wheelcam.geometry import build_wheel, export_model, inspect_shape
from wheelcam.models import WheelSpec


@pytest.mark.parametrize("overrides", [
    {},
    {"spoke_count": 5, "sweep_deg": -18, "outer_diameter_mm": 380, "width_mm": 150, "dish_mm": 55},
    {"spoke_count": 10, "sweep_deg": 18, "outer_diameter_mm": 600, "width_mm": 280, "spoke_width_mm": 52},
])
def test_connected_solid_and_functional_holes(overrides):
    spec = WheelSpec(**overrides)
    shape = build_wheel(spec).val()
    report = inspect_shape(shape, spec)
    assert all(report["checks"].values())
    solid = shape.Solids()[0]
    hub_z = spec.width_mm / 2 - 14 - spec.spoke_thickness_mm / 2 - spec.dish_mm
    # Independent point probes verify actual voids, not just the input recipe.
    assert not solid.isInside((0, 0, hub_z))
    assert not solid.isInside((spec.center_bore_mm / 2 - 0.1, 0, hub_z))
    assert solid.isInside((spec.center_bore_mm / 2 + 0.1, 0, hub_z))
    for index in range(spec.bolt_count):
        angle = 2 * math.pi * index / spec.bolt_count
        x = spec.bolt_circle_mm / 2 * math.cos(angle)
        y = spec.bolt_circle_mm / 2 * math.sin(angle)
        assert not solid.isInside((x, y, hub_z))
        outside_hole = spec.bolt_circle_mm / 2 + spec.bolt_diameter_mm / 2 + 0.1
        assert solid.isInside((outside_hole * math.cos(angle), outside_hole * math.sin(angle), hub_z))


@pytest.mark.parametrize("overrides", [
    {"center_bore_mm": 90, "bolt_circle_mm": 100},
    {"bolt_circle_mm": 140, "hub_diameter_mm": 140},
    {"spoke_count": 5.5}, {"spoke_count": True},
    {"width_mm": float("nan")}, {"outer_diameter_mm": -1},
    {"surprise_field": 1},
])
def test_invalid_spec_rejected_before_kernel(overrides):
    with pytest.raises(ValidationError):
        WheelSpec(**overrides)


def test_export_artifacts_preserve_solid(tmp_path):
    spec = WheelSpec()
    report = export_model(spec, tmp_path)
    assert report["checks"]["step_roundtrip"]
    imported = cq.importers.importStep(str(tmp_path / "wheel.step")).val()
    assert len(imported.Solids()) == 1
    assert imported.BoundingBox().xlen == pytest.approx(480, abs=1e-4)
    data = (tmp_path / "wheel.glb").read_bytes()
    magic, version, length = struct.unpack("<III", data[:12])
    assert (magic, version, length) == (0x46546C67, 2, len(data))
    json_length, kind = struct.unpack("<II", data[12:20])
    gltf = json.loads(data[20:20 + json_length])
    assert gltf["meshes"]
    assert any(accessor.get("count", 0) > 100 for accessor in gltf["accessors"])
    assert report["engineering_approved"] is False
