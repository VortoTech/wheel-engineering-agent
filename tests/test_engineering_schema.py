import json

import pytest
from pydantic import ValidationError

from wheelcam.engineering_schema import EngineeringParameter, build_engineering_definition
from wheelcam.models import WheelSpec, default_sources


def test_unknown_is_a_first_class_state_not_a_plausible_number():
    value = EngineeringParameter(value=None, unit="mm", source="unknown", confidence=0, critical=True)
    assert value.value is None
    with pytest.raises(ValidationError, match="UNKNOWN"):
        EngineeringParameter(value=24, unit="mm", source="unknown", confidence=.4)
    with pytest.raises(ValidationError, match="非 UNKNOWN"):
        EngineeringParameter(value=None, unit="mm", source="inferred", confidence=.4)


def test_existing_spec_maps_to_evidence_aware_engineering_definition():
    sources = default_sources()
    sources["rim_diameter_in"] = {"kind": "measurement", "note": "用户提供 19 英寸规格"}
    definition = build_engineering_definition(WheelSpec(rim_diameter_in=19), sources)
    assert definition.parameters["rim_diameter_in"].source == "specified"
    assert definition.parameters["rim_diameter_in"].confidence == 1
    assert definition.parameters["rim_diameter_in"].unit == "in"
    assert "center_bore_mm" in definition.critical_unconfirmed
    assert definition.manufacturing_status == "not_released"
    assert definition.engineering_approved is False
    assert [item.id for item in definition.feature_tree][:5] == [
        "rim", "hub", "center_bore", "bolt_master", "bolt_pattern"]
    assert definition.feature_tree[7].depends_on == ["rim", "hub", "spoke_pattern"]


def test_unobservable_dimensions_can_be_explicitly_unknown():
    definition = build_engineering_definition(
        WheelSpec(), default_sources(), unknown_fields={"hub_thickness_mm", "spoke_thickness_mm"})
    assert definition.parameters["hub_thickness_mm"].value is None
    assert definition.parameters["spoke_thickness_mm"].source == "unknown"
    assert definition.critical_unknowns == ["hub_thickness_mm", "spoke_thickness_mm"]
    dumped = json.loads(definition.model_dump_json())
    assert dumped["parameters"]["hub_thickness_mm"]["value"] is None
