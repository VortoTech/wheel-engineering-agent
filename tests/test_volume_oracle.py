import hashlib
import json
import math

import numpy as np
import pytest

from scripts.validate_volume_oracle import capsule_integral, compare_measurements, frozen_inputs, run, slot_edit_oracle
from wheelcam.models import WheelSpec
from wheelcam.sector_program import RootSlot, example_program, patch_feature


def fixture():
    base = WheelSpec(junction_fillet_mm=0, window_edge_fillet_mm=0,
                     pocket_depth_mm=0, valve_diameter_mm=0, center_bore_mm=66.6)
    before = example_program()
    return before, patch_feature(before, "root_slot_left", {"width_mm": 8}), base


@pytest.mark.parametrize("width", [3, 6, 8, 15])
def test_capsule_quadrature_matches_analytic_constant_thickness(width):
    slot = RootSlot(id="slot", center_radius_mm=102, center_angle_deg=8, length_mm=22, width_mm=width)
    actual = capsule_integral(slot, lambda radial: np.full_like(radial, 6), 32)
    expected = (width * (22 - width) + math.pi * (width / 2) ** 2) * 6
    assert actual == pytest.approx(expected, abs=1e-9)


def test_fixture_oracle_converges_to_independent_reference():
    result = slot_edit_oracle(*fixture())
    assert result["expected_removed_mm3"] == pytest.approx(5012.72037, abs=1e-5)
    assert result["converged"] and result["convergence_delta_mm3"] < 1e-6
    assert result["groups"] == 5
    assert result["manufacturing_status"] == "not_released"


@pytest.mark.parametrize("field", ["junction_fillet_mm", "window_edge_fillet_mm", "window_face_relief_mm",
                                   "window_side_draft_deg", "pocket_depth_mm", "valve_diameter_mm"])
def test_oracle_rejects_finishing_operations(field):
    before, after, base = fixture()
    data = base.model_dump()
    data[field] = {"valve_diameter_mm": 8, "pocket_depth_mm": 12}.get(field, 1)
    with pytest.raises(ValueError, match="excludes"):
        slot_edit_oracle(before, after, WheelSpec.model_validate(data))


def test_oracle_rejects_other_edits_and_width_decrease():
    before, after, base = fixture()
    for candidate in (before, patch_feature(before, "root_slot_left", {"width_mm": 5}),
                      patch_feature(after, "root_slot_left", {"length_mm": 23}),
                      patch_feature(after, "root_slot_right", {"width_mm": 7})):
        with pytest.raises(ValueError, match="width|exactly one"):
            slot_edit_oracle(before, candidate, base)


def test_oracle_rejects_topology_change():
    before, after, base = fixture()
    with pytest.raises(ValueError, match="topology"):
        slot_edit_oracle(before, after.model_copy(update={"phase_deg": 1}), base)


def test_oracle_refuses_existing_evidence(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    saved = output / "oracle.json"
    saved.write_text("old")
    with pytest.raises(FileExistsError, match="overwrite"):
        run(tmp_path / "absent", output)
    assert saved.read_text() == "old"


@pytest.mark.parametrize("filename", ["wheel.step", "recipe.json"])
def test_frozen_evidence_checks_both_geometry_and_recipe_hashes(tmp_path, filename):
    folder = tmp_path / "b-program-replay"
    folder.mkdir()
    for name in ("wheel.step", "recipe.json", "sector-program.json"):
        (folder / name).write_text("{}")
    digest = hashlib.sha256(b"{}").hexdigest()
    (folder / "report.json").write_text(json.dumps({"artifacts": {
        name: {"sha256": digest} for name in ("wheel.step", "recipe.json")}}))
    (folder / filename).write_text('{"tampered": true}')
    with pytest.raises(ValueError, match="frozen report hash"):
        frozen_inputs(tmp_path)


def test_measurement_comparison_requires_frozen_step_hashes():
    oracle = {"converged": True, "expected_removed_mm3": 12.3}
    inputs = [{"case": key, "sha256": {"wheel.step": key}} for key in ("b-program-replay", "c-slot-edit")]
    report = {"schema": "wheel-volume-measurements-v1", "measurements": [
        {"case": item["case"], "step_sha256": item["case"], "volume_mm3": volume, "method": "test"}
        for item, volume in zip(inputs, [100, 87.7])]}
    assert compare_measurements(oracle, inputs, report)["passed"]
    report["measurements"][1]["step_sha256"] = "wrong"
    with pytest.raises(ValueError, match="hash"):
        compare_measurements(oracle, inputs, report)


def test_measurement_comparison_rejects_disagreement_without_changing_allowance():
    oracle = {"converged": True, "expected_removed_mm3": 12.3}
    inputs = [{"case": key, "sha256": {"wheel.step": key}} for key in ("b-program-replay", "c-slot-edit")]
    report = {"schema": "wheel-volume-measurements-v1", "measurements": [
        {"case": item["case"], "step_sha256": item["case"], "volume_mm3": volume, "method": "test"}
        for item, volume in zip(inputs, [100, 80])]}
    result = compare_measurements(oracle, inputs, report)
    assert not result["passed"]
    assert result["comparison_allowance_mm3"] == .5
