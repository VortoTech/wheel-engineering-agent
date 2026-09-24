import json

import pytest

from scripts import validate_parametric_matrix as validation
from scripts.validate_parametric_matrix import build_acceptance, coverage_labels, matrix, operation_matrix
from wheelcam.windows import spoke_ridge_outlines


def test_p1_matrix_defines_sixty_distinct_valid_parameter_sets():
    cases = list(matrix())
    assert len(cases) == 60
    serialized = {case.model_dump_json() for case in cases}
    assert len(serialized) == 60
    assert {case.rim_diameter_in for case in cases} == {17, 18, 19, 20, 21, 22}
    assert {case.spoke_count for case in cases} == {5, 6, 7, 8, 9, 10}
    assert {case.bolt_count for case in cases} == {4, 5, 6}


def test_operation_suite_covers_active_geometry_paths_with_synthetic_sketches():
    cases = dict(operation_matrix())
    assert len(cases) == 8
    assert set(cases) == {"loft-single", "loft-paired", "window-base", "window-relief", "window-ridge",
                          "window-draft", "window-combined", "window-rim-pockets"}
    assert cases["loft-single"].spoke_style == "single"
    assert cases["loft-paired"].spoke_style == "paired"
    assert cases["window-relief"].window_face_relief_mm > 0
    assert cases["window-draft"].window_side_draft_deg > 0
    for case in (cases["window-ridge"], cases["window-combined"]):
        assert len(spoke_ridge_outlines(case.window_outlines_mm, case.spoke_count)) == 2
        assert case.window_spoke_ridge_mm > 0
    assert cases["window-rim-pockets"].rim_pocket_count == 15
    assert dict(operation_matrix()) == cases


def test_window_coverage_uses_actual_window_topology_not_inactive_style_field():
    cases = dict(operation_matrix())
    labels = coverage_labels(cases["window-combined"])
    assert labels["method"] == "window"
    assert labels["style"] == "three_window"
    assert {"window_cut", "face_relief", "spoke_ridge", "side_draft"} <= set(labels["features"])
    assert "back_pocket" not in labels["features"]
    assert "spoke_loft" not in labels["features"]


def report(**changes):
    """Unit-test fixture only: this is not evidence of an actual CAD export."""
    return {"checks": {"valid": True, "step_roundtrip": True}, "step_solid_count": 1,
            "step_volume_relative_delta": 1e-8, "build_status": "exact",
            "step_stability": {"fallback_used": False}, **changes}


@pytest.mark.parametrize("changes, status, geometry_passed", [
    ({}, "exact_requested", True),
    ({"build_status": "degraded"}, "degraded", True),
    ({"step_stability": {"fallback_used": True}}, "degraded", True),
    ({"junction_fillet_requested_mm": 5, "junction_fillet_applied_mm": 3}, "degraded", True),
    ({"window_method": {"spoke_ridge": {"requested_mm": 2, "applied_mm": 0}}}, "degraded", True),
    ({"window_edge_fillet_requested_mm": 2, "window_edge_fillet_applied_mm": 2,
      "window_edges_rounded": 3, "window_edges_total": 8}, "degraded", True),
    ({"checks": {"valid": False, "step_roundtrip": True}}, "failed", False),
    ({"checks": {}}, "failed", False),
    ({"checks": {"valid": True}}, "failed", False),
    ({"step_solid_count": 2}, "failed", False),
    ({"build_status": None}, "unverified", True),
    ({"build_status": None, "junction_fillet_requested_mm": 5,
      "junction_fillet_applied_mm": 5}, "unverified", True),
])
def test_geometry_success_does_not_hide_degraded_or_missing_build_evidence(changes, status, geometry_passed):
    result = build_acceptance(report(**changes))
    assert result["build_status"] == status
    assert result["geometry_passed"] is geometry_passed
    assert result["exact_requested"] is (status == "exact_requested")


def test_mocked_export_summary_keeps_geometry_and_exact_acceptance_separate(tmp_path, monkeypatch):
    reports = iter([report(), report(build_status="degraded"), report(build_status=None)])
    monkeypatch.setattr(validation, "export_model", lambda *_args, **_kwargs: next(reports))
    result = validation.run(tmp_path, 3, suite="operations")
    assert result["requested"] == 3
    assert result["passed"] == result["geometry_passed"] == 3
    assert result["exact_requested"] == result["degraded"] == result["unverified"] == 1
    assert result["failed"] == 0
    assert result["acceptance_passed"] is False
    assert result["coverage"]["by_method"]["loft"]["selected"] == 2
    assert result["coverage"]["by_method"]["window"]["unverified"] == 1
    assert result["coverage"]["by_feature"]["paired_slot"]["degraded"] == 1
    assert result["manufacturing_status"] == "not_released"
    assert result["engineering_approved"] is False
    assert json.loads((tmp_path / "summary.json").read_text())["scope"].startswith("Selected synthetic")


def test_mocked_export_exception_is_counted_as_failure_without_aborting_suite(tmp_path, monkeypatch):
    def fail_export(*_args, **_kwargs):
        raise ValueError("synthetic test error")

    monkeypatch.setattr(validation, "export_model", fail_export)
    result = validation.run(tmp_path, 1)
    assert result["requested"] == result["failed"] == 1
    assert result["geometry_passed"] == result["exact_requested"] == 0
    assert result["coverage"]["by_method"]["loft"]["failed"] == 1
    assert "ValueError: synthetic test error" in result["cases"][0]["error"]


def test_existing_evidence_is_never_overwritten(tmp_path, monkeypatch):
    evidence = tmp_path / "case-001"
    evidence.mkdir()
    (evidence / "wheel.step").write_text("previous artifact")
    monkeypatch.setattr(validation, "export_model", lambda *_args, **_kwargs: pytest.fail("must not export"))
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        validation.run(tmp_path, 1)
    assert (evidence / "wheel.step").read_text() == "previous artifact"


@pytest.mark.parametrize("suite, limit", [("invalid", 1), ("operations", 9), ("legacy", 0)])
def test_invalid_suite_request_fails_before_creating_output(tmp_path, suite, limit):
    output = tmp_path / "not-created"
    with pytest.raises(ValueError):
        validation.run(output, limit, suite)
    assert not output.exists()
