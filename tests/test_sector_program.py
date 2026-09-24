"""Kernel-free design-program contracts; actual CAD tests are separate."""
import copy
import math

import pytest
from pydantic import ValidationError

from wheelcam.agent_cad import ConfirmedEvidenceConflict
from wheelcam.models import WheelSpec, default_sources
from wheelcam.sector_program import (
    OUTLINE_SAMPLES, SectorProgram, compile_sector, example_program,
    feature_outline, patch_feature, program_hash, sample_feature,
)
from wheelcam.windows import MIN_WEB_MM, self_intersects


@pytest.mark.parametrize("family", ["single", "split_y"])
@pytest.mark.parametrize("groups", range(5, 11))
def test_design_families_compile_complete_outlines(family, groups):
    program = example_program(family, groups)
    spec, manifest = compile_sector(program, WheelSpec())
    assert spec.spoke_count == groups
    assert spec.spoke_method == "window"
    assert len(spec.window_outlines_mm) == (4 if family == "split_y" else 1)
    assert all(len(outline) == OUTLINE_SAMPLES for outline in spec.window_outlines_mm)
    assert all(not self_intersects(outline) for outline in spec.window_outlines_mm)
    assert manifest["sampled_outline_checks"]["min_web_mm"] >= MIN_WEB_MM
    assert manifest["provenance"] == "design_assumption"
    assert manifest["native_spline_fidelity"] == manifest["photo_fidelity"] == "not_validated"
    assert manifest["manufacturing_status"] == "not_released"
    assert list(manifest["feature_map"]) == [feature.id for feature in program.features]


def test_feature_edit_is_local_and_immutable_with_deterministic_hash():
    program = example_program()
    before = program.model_dump(mode="json")
    candidate = patch_feature(program, "root_slot_left", {"width_mm": 8})
    old, old_manifest = compile_sector(program, WheelSpec())
    new, new_manifest = compile_sector(candidate, WheelSpec())
    assert program.model_dump(mode="json") == before
    assert program_hash(program) == program_hash(before)
    assert program_hash(program) != program_hash(candidate)
    assert old_manifest["feature_map"].keys() == new_manifest["feature_map"].keys()
    for index, feature in enumerate(program.features):
        assert (old.window_outlines_mm[index] != new.window_outlines_mm[index]) == (feature.id == "root_slot_left")
    with pytest.raises(ValidationError, match="frozen"):
        program.features[0].root_radius_mm = 120


@pytest.mark.parametrize("field", ["spoke_count", "spoke_phase_deg", "spoke_method", "spoke_style", "window_outlines_mm"])
@pytest.mark.parametrize("source_kind", ["manual", "drawing", "measurement", "specified"])
def test_compile_protects_confirmed_owned_fields(field, source_kind):
    sources = default_sources()
    sources[field]["kind"] = source_kind
    with pytest.raises(ConfirmedEvidenceConflict) as error:
        compile_sector(example_program(), WheelSpec(), sources)
    assert error.value.conflicts[0]["target"] == field


def test_compile_preserves_unowned_dimensions_and_source_inputs():
    sources = default_sources()
    sources["center_bore_mm"]["kind"] = "measurement"
    before = copy.deepcopy(sources)
    base = WheelSpec(center_bore_mm=67, spoke_thickness_mm=30, spoke_crown_mm=3)
    spec, manifest = compile_sector(example_program(), base, sources)
    for field, value in base.model_dump().items():
        if field not in manifest["owned_spec_fields"]:
            assert spec.model_dump()[field] == value
    assert sources == before
    assert base.spoke_method == "loft"


@pytest.mark.parametrize("changes, reason", [
    ({"root_radius_mm": 86, "tip_half_width_mm": 80}, "hub keep"),
    ({"tip_radius_mm": 230}, "outer rim"),
])
def test_no_silent_clipping_at_hub_or_rim(changes, reason):
    candidate = patch_feature(example_program(), "main_window", changes)
    with pytest.raises(ValueError, match=reason):
        compile_sector(candidate, WheelSpec())


def test_orbit_and_front_web_checks_are_not_bypassed():
    program = example_program()
    overlap = patch_feature(program, "root_slot_left", {"center_angle_deg": -8})
    with pytest.raises(ValidationError, match="重叠"):
        compile_sector(overlap, WheelSpec())
    narrow = patch_feature(program, "root_slot_left", {"center_angle_deg": -4})
    with pytest.raises(ValidationError, match="过窄"):
        compile_sector(narrow, WheelSpec())


@pytest.mark.parametrize("changes", [
    {"id": "rewritten"}, {"kind": "root_slot"}, {"role": "split_window"},
    {"points": [[0, 0]]}, {"width_mm": float("nan")}, {"width_mm": 22}, {},
])
def test_feature_edits_cannot_inject_code_coordinates_or_invalid_values(changes):
    with pytest.raises(ValueError):
        patch_feature(example_program(), "root_slot_left", changes)
    with pytest.raises(ValueError, match="Unknown sector feature"):
        patch_feature(example_program(), "not_there", {"width_mm": 7})


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(groups=True),
    lambda data: data.update(groups=11),
    lambda data: data.update(provenance="observed"),
    lambda data: data.update(provenance="unknown"),
    lambda data: data.update(family="single"),
    lambda data: data["features"][1].update(id="main_window"),
    lambda data: data["features"][0].update(root_radius_mm=None),
    lambda data: data["features"][0].update(sweep_deg=float("inf")),
])
def test_schema_is_bounded_and_does_not_fabricate_observation(mutation):
    data = example_program().model_dump(mode="json")
    mutation(data)
    with pytest.raises(ValidationError):
        SectorProgram.model_validate(data)


def test_validates_forged_pydantic_instances_instead_of_trusting_model_copy():
    invalid = example_program().model_copy(update={"groups": 100})
    with pytest.raises(ValidationError):
        compile_sector(invalid, WheelSpec())
    base = WheelSpec().model_copy(update={"spoke_thickness_mm": -2})
    with pytest.raises(ValidationError):
        compile_sector(example_program(), base)


def test_no_duplicate_endpoints_and_straight_capsule_sides_are_sampled():
    program = example_program()
    for feature in program.features:
        points = feature_outline(feature)
        assert len(points) == len(set(points)) == OUTLINE_SAMPLES
        assert points[0] != points[-1]
        assert len(sample_feature(feature, 1024)) == 1024
    slot = program.features[2]
    points = sample_feature(slot)
    a = math.radians(-slot.center_angle_deg)
    aligned = [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)) for x, y in points]
    assert sum(abs(y - slot.width_mm / 2) < 1e-8 for _, y in aligned) >= OUTLINE_SAMPLES // 4
    assert sum(abs(y + slot.width_mm / 2) < 1e-8 for _, y in aligned) >= OUTLINE_SAMPLES // 4


def test_unsupported_legacy_ridge_is_rejected_instead_of_changed_silently():
    with pytest.raises(ValueError, match="legacy three-window"):
        compile_sector(example_program(), WheelSpec(window_spoke_ridge_mm=1))
