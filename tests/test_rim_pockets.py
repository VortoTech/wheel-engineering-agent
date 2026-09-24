import json
import math

import pytest
from pydantic import ValidationError

from wheelcam.geometry import build_wheel
from wheelcam.models import WheelSpec, default_sources, migrate_spec
from wheelcam.preparation import feature_manifest
from wheelcam.template import layout


def pocket_spec(**changes):
    return WheelSpec.model_validate({**WheelSpec().model_dump(),
        "rim_pocket_count": 15, "rim_pocket_phase_deg": 0,
        "rim_pocket_radial_mm": 30, "rim_pocket_width_mm": 14,
        "rim_pocket_depth_mm": 7, "rim_pocket_inset_mm": 7,
        "rim_pocket_corner_mm": 3, **changes})


def test_rim_pockets_have_an_independent_period_and_real_blind_floors():
    spec = pocket_spec(spoke_count=5)
    wheel, _ = build_wheel(spec)
    solid = wheel.val().Solids()[0]
    band = layout(spec)["rim_pocket_band"]
    assert band["count"] == 15 and spec.spoke_count == 5
    r, front = band["pocket_center_radius_mm"], band["front_z_mm"]
    assert not solid.isInside((r, 0, front - 1))
    assert solid.isInside((r, 0, front - band["depth_mm"] - 1))
    between = math.radians(360 / band["count"] / 2)
    assert solid.isInside((r * math.cos(between), r * math.sin(between), front - 1))
    assert wheel.val().isValid() and len(wheel.val().Solids()) == 1


def test_rim_pocket_constraints_and_feature_manifest():
    with pytest.raises(ValidationError):
        pocket_spec(rim_pocket_corner_mm=8, rim_pocket_width_mm=14)
    spec = pocket_spec(rim_pocket_phase_deg=6)
    features = feature_manifest(spec, {}, "sha")["features"]
    pockets = [feature for feature in features if feature["kind"] == "front_rim_pocket"]
    assert len(pockets) == 15
    assert pockets[0]["parameters"]["rotation_deg"] == 6
    assert pockets[-1]["parameters"]["rotation_deg"] == pytest.approx(342)


def test_v11_migration_keeps_v10_shape_when_pockets_are_disabled():
    current = WheelSpec().model_dump()
    old = {key: value for key, value in current.items() if not key.startswith("rim_pocket_")}
    sources = {key: {"kind": "manual", "note": "preserve"} for key in old}
    migrated, new_sources = migrate_spec(old, sources)
    assert migrated["rim_pocket_count"] == 0
    assert all(migrated[key] == value and new_sources[key] == sources[key] for key, value in old.items())
    assert json.loads(WheelSpec(**migrated).model_dump_json())["rim_pocket_count"] == 0
