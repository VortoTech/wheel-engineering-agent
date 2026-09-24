from unittest.mock import Mock
import hashlib
import json

import cadquery as cq
import pytest

from scripts.validate_sector_program import canonical_hash, difference, read_verified_build, run, shape_volume, volume_stability
from wheelcam.models import WheelSpec


def test_comparison_never_replaces_evidence(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    old = output / "summary.json"
    old.write_text("existing result")
    with pytest.raises(FileExistsError, match="overwrite"):
        run(output)
    assert old.read_text() == "existing result"


def test_recipe_hash_ignores_key_order_but_not_values():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


@pytest.mark.parametrize("null, valid", [(True, True), (False, False)])
def test_invalid_boolean_cannot_look_like_zero_change(null, valid):
    result = Mock()
    result.isNull.return_value = null
    result.isValid.return_value = valid
    with pytest.raises(ValueError, match="Invalid/null Boolean"):
        shape_volume(result)


def test_empty_valid_compound_is_a_legitimate_zero_difference():
    before = cq.Workplane("XY").box(10, 10, 10).val()
    _, _, values = difference(before, before)
    assert values["removed_mm3"] == 0
    assert values["added_mm3"] == 0
    assert values["conservation_residual_mm3"] < 1e-8


def test_actual_subtractive_difference_conserves_volume():
    before = cq.Workplane("XY").box(10, 10, 10).val()
    tool = cq.Workplane("XY").circle(1).extrude(20, both=True).val()
    after = before.cut(tool)
    _, _, values = difference(before, after)
    assert values["removed_mm3"] == pytest.approx(10 * 3.141592653589793, abs=1e-6)
    assert values["added_mm3"] < 1e-8
    assert values["conservation_residual_mm3"] < 1e-8


def test_incomplete_boolean_fails_mass_conservation(monkeypatch):
    from scripts import validate_sector_program as comparison

    # Simulate a kernel returning plausible empty compounds for every Boolean.
    first, second, empty = Mock(), Mock(), Mock()
    monkeypatch.setattr(comparison, "compare_boolean", lambda *args, **kwargs: (empty, {}))
    monkeypatch.setattr(comparison, "shape_volume", lambda obj: 100 if obj is first else 90 if obj is second else 0)
    with pytest.raises(comparison.BooleanComparisonError, match="conservation") as error:
        difference(first, second)
    assert error.value.details["passed"] is False
    assert error.value.details["conservation_residual_mm3"] == 100
    assert error.value.details["conservation_residuals"]["after_minus_common_minus_added_mm3"] == 90


def test_simple_solid_integration_is_stable_across_tolerances():
    result = volume_stability(cq.Workplane("XY").box(10, 10, 10).val())
    assert result["passed"]
    assert result["relative_spread"] < 1e-10
    assert len(result["measurements"]) == 3
    assert all(item["volume_mm3"] == pytest.approx(1000) for item in result["measurements"])


@pytest.mark.parametrize("tamper", [None, "recipe", "step", "resolved", "status"])
def test_reused_step_binds_recipe_hash_and_resolved_input(tmp_path, tamper):
    spec = WheelSpec()
    recipe = tmp_path / "recipe.json"
    step = tmp_path / "wheel.step"
    # This test concerns provenance; native STEP geometry is inspected separately.
    recipe.write_text(json.dumps({"spec": spec.model_dump(mode="json")}))
    step.write_bytes(b"step bytes for hash fixture")
    report = {"artifacts": {name: {"sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}
                            for name in ("recipe.json", "wheel.step")},
              "build_status": "exact", "build_resolution": {"resolved_recipe": spec.model_dump(mode="json")}}
    if tamper == "recipe":
        recipe.write_text(recipe.read_text() + " ")
    elif tamper == "step":
        step.write_bytes(b"changed")
    elif tamper == "resolved":
        report["build_resolution"]["resolved_recipe"]["center_bore_mm"] += 1
    elif tamper == "status":
        report["build_status"] = "degraded"
    (tmp_path / "report.json").write_text(json.dumps(report))
    if tamper:
        with pytest.raises(ValueError):
            read_verified_build(tmp_path, spec)
    else:
        assert read_verified_build(tmp_path, spec) == report


def test_volume_comparison_does_not_change_operands():
    before = cq.Workplane("XY").box(10, 10, 10).val()
    after = before.copy().cut(cq.Workplane("XY").circle(1).extrude(20, both=True).val())
    volumes = shape_volume(before), shape_volume(after)
    first = difference(before, after)[2]
    second = difference(before, after)[2]
    # Per-operation elapsed time is diagnostic, not a geometric invariant.
    assert {key: value for key, value in first.items() if key != "operations"} == {
        key: value for key, value in second.items() if key != "operations"}
    assert (shape_volume(before), shape_volume(after)) == volumes
