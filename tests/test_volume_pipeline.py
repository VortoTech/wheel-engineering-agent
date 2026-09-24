"""The CAD/preparation entry points share one auditable numerical policy."""
import math
from types import SimpleNamespace

import cadquery as cq
import pytest

from wheelcam.geometry import _volume, _window_outline_wire
from wheelcam.mass_properties import measure_volume, volume_method
from wheelcam.models import MaterialSpec, Preparation, StockSpec, WheelSpec
from wheelcam.preparation import check_preparation, checked_volume, volume
from wheelcam.sector_program import example_program, sample_feature


def capsule():
    feature = next(item for item in example_program().features if item.id == "root_slot_left")
    return cq.Solid.extrudeLinear(_window_outline_wire(sample_feature(feature)), [], cq.Vector(0, 0, 6))


def test_legacy_entry_points_use_same_span_integrator():
    body = capsule()
    expected = (6 * (22 - 6) + math.pi * 3 ** 2) * 6
    measurement = measure_volume(body)
    assert measurement.volume_mm3 == pytest.approx(expected, abs=.005)
    assert _volume(body) == volume(body) == checked_volume(body) == measurement.volume_mm3


def test_preparation_weight_and_removal_use_new_volume_without_changing_input():
    body = capsule()
    before = measure_volume(body).volume_mm3
    stock = StockSpec(outer_diameter_mm=300, height_mm=40, cavity_diameter_mm=0, front_web_mm=20)
    prep = Preparation(stock=stock, material=MaterialSpec(name="analytic test fixture", density_kg_m3=2700))
    result = check_preparation(body, WheelSpec(), prep)
    assert result["stock"]["status"] == "contained"
    assert result["stock"]["finished_volume_mm3"] == pytest.approx(before, abs=.0005)
    assert result["weight"]["finished_kg"] == round(before * 2700 / 1e9, 4)
    assert result["stock"]["removed_volume_mm3"] == pytest.approx(
        math.pi * 150 ** 2 * 40 - before, abs=.001)
    assert all(section["volume_method"] == volume_method() for section in result.values())
    assert body.isValid() and measure_volume(body).volume_mm3 == before


def test_preparation_rejects_null_shape_instead_of_zero_missing_material():
    with pytest.raises(ValueError):
        checked_volume(SimpleNamespace(isNull=lambda: True))
