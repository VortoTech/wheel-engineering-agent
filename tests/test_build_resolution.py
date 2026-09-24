import hashlib
import json

import cadquery as cq
import pytest

from wheelcam.build_resolution import resolve_build
from wheelcam.engineering_schema import build_engineering_definition
from wheelcam.models import WheelSpec, default_sources


def outcome(spec, **values):
    return {"hub_fillet_applied_mm": spec.junction_fillet_mm,
            "rim_fillet_applied_mm": spec.junction_fillet_mm, **values}


def test_junction_results_do_not_hide_different_applied_radii():
    spec = WheelSpec()
    result = resolve_build(spec, spec, outcome(spec, hub_fillet_applied_mm=2))
    assert result.status == "degraded" and result.review_required
    assert result.feature_results[0].requested == 5
    assert result.feature_results[0].applied == 2
    assert result.feature_results[1].applied == 5
    assert result.requested_spec["junction_fillet_mm"] == 5


def test_unreported_result_is_not_exact():
    result = resolve_build(WheelSpec(), WheelSpec(), {})
    assert result.status == "unverified" and result.review_required


def test_unknown_evidence_does_not_become_known_through_building():
    spec = WheelSpec()
    sources = default_sources()
    sources["spoke_thickness_mm"] = {"kind": "unknown", "confidence": 0}
    resolution = resolve_build(spec, spec, outcome(spec))
    definition = build_engineering_definition(spec, sources, build_resolution=resolution)
    assert definition.parameters["spoke_thickness_mm"].value is None
    assert definition.build_resolution.resolved_recipe["spoke_thickness_mm"] == 28
    assert definition.manufacturing_status == "not_released"


def test_clamped_pocket_sections_are_recorded_as_adjusted():
    spec = WheelSpec(pocket_depth_mm=24)
    result = resolve_build(spec, spec, outcome(spec))
    pocket = next(item for item in result.feature_results if item.feature == "back_pockets")
    assert pocket.requested == 24
    assert pocket.applied == pytest.approx([20.152, 17.8, 15.448])
    assert pocket.status == "adjusted" and result.status == "degraded"


def test_explicit_source_confidence_is_not_upgraded():
    definition = build_engineering_definition(WheelSpec(), {
        "center_bore_mm": {"kind": "measurement", "confidence": .3}})
    assert definition.parameters["center_bore_mm"].confidence == .3


def test_partial_window_rounding_and_approximate_draft_are_not_exact():
    from scripts.validate_parametric_matrix import operation_matrix
    spec = dict(operation_matrix())["window-draft"].model_copy(update={"window_edge_fillet_mm": 2})
    info = outcome(spec, window_edge_fillet_applied_mm=2, window_edges_total=20,
                   window_edges_rounded=10, spoke_ridge={"applied_mm": 0})
    result = resolve_build(spec, spec, info)
    by_feature = {item.feature: item for item in result.feature_results}
    assert by_feature["window_edges"].status == "partial"
    assert by_feature["window_side_draft"].applied is None
    assert by_feature["window_side_draft"].status == "unverified"
    assert result.status == "degraded"


def test_export_fallback_binds_evidence_and_final_recipe(tmp_path, monkeypatch):
    """Fast export orchestration regression; box fixtures are NOT real wheel acceptance."""
    from wheelcam import geometry, appearance
    spec = WheelSpec()
    requested_sources = default_sources()
    requested_sources["junction_fillet_mm"] = {"kind": "measurement", "confidence": .7, "note": "fixture"}
    seen = []

    def build(candidate):
        shape = cq.Workplane("XY").box(10, 10, candidate.junction_fillet_mm + 5)
        info = outcome(candidate, junction_fillet_requested_mm=candidate.junction_fillet_mm,
                       junction_fillet_applied_mm=candidate.junction_fillet_mm)
        return shape, info, shape.val()

    def inspect(shape, candidate):
        seen.append(candidate.junction_fillet_mm)
        return {"checks": {"valid_brep": shape.isValid()}, "volume_mm3": shape.Volume(),
                "solid_count": len(shape.Solids())}

    real_roundtrip = geometry._export_step_roundtrip
    attempts = []

    def roundtrip(shape, path):
        imported, delta = real_roundtrip(shape, path)
        attempts.append(delta)
        return imported, .01 if len(attempts) == 1 else delta

    def previews(shape, rim, candidate, output):
        seen.append(candidate.junction_fillet_mm)
        cq.Assembly(shape).export(str(output / "wheel.glb"))
        return {"status": "display_only"}

    monkeypatch.setattr(geometry, "_build_wheel", build)
    monkeypatch.setattr(geometry, "inspect_shape", inspect)
    monkeypatch.setattr(geometry, "_export_step_roundtrip", roundtrip)
    monkeypatch.setattr(appearance, "export_previews", previews)
    report = geometry.export_model(spec, tmp_path, snapshot={
        "spec": spec.model_dump(mode="json"), "sources": requested_sources})
    engineering = json.loads((tmp_path / "engineering.json").read_text())
    handoff = json.loads((tmp_path / "features.json").read_text())
    resolution = report["build_resolution"]
    assert report["build_status"] == "degraded"
    assert resolution["requested_spec"]["junction_fillet_mm"] == 5
    assert resolution["resolved_recipe"]["junction_fillet_mm"] == 3.75
    assert engineering["parameters"]["junction_fillet_mm"]["value"] == 5
    assert engineering["parameters"]["junction_fillet_mm"]["confidence"] == .7
    assert engineering["build_resolution"] == resolution == handoff["build_resolution"]
    assert handoff["spec"]["junction_fillet_mm"] == 5
    assert handoff["sources"]["junction_fillet_mm"]["kind"] == "measurement"
    assert handoff["resolved_recipe"]["junction_fillet_mm"] == 3.75
    assert seen == [5, 3.75, 3.75, 3.75]
    assert report["artifacts"]["engineering.json"]["sha256"] == hashlib.sha256(
        (tmp_path / "engineering.json").read_bytes()).hexdigest()
