"""The STEP-backed study must use the actual M59 STEP pair and coordinate datum."""
import json
import math
import re
from pathlib import Path

import pytest

from wheelcam.manufacturing_step_demo import create_step_package


def test_m59_step_pair_drives_simulation_and_reference_nc(tmp_path):
    root = Path(__file__).resolve().parents[1]
    case = root / "runs/real-orders-eval/machining/case-03-d20w10.5"
    spec = root / "runs/real-orders/case-03/d20w10.5/spec.json"
    if not (case / "machining.step").is_file() or not spec.is_file():
        pytest.skip("Private M59 machining STEP fixture unavailable")
    output = tmp_path / "m59"
    plan = create_step_package(case, output, spec_path=spec)
    assert plan["source"]["stock.step"]["sha256"] != plan["source"]["machining.step"]["sha256"]
    assert plan["geometry"]["machining_mesh_vs_report_volume_fraction"] < .001
    assert plan["simulation"]["gouge_vs_machining_step_mm3"] < 1
    assert plan["simulation"]["remaining_vs_machining_step_mm3"] > 0
    assert plan["simulation"]["step_pair_mesh_baseline_mismatch_mm3"] > 0
    assert (output / "stock_step.glb").stat().st_size > 1000
    assert (output / "machining_step.glb").stat().st_size > 1000
    nc = (output / "reference.nc").read_text()
    assert "Z0 RIM WIDTH MIDPLANE" in nc
    assert "TURNING CONTOUR: USE STOCK.STEP" in nc
    assert "TURN CONTOUR REFERENCE" not in nc
    assert nc.count("(G81 X") == 5
    assert all(line.startswith("(") and line.endswith(")") for line in nc.splitlines())
    g81 = next(line for line in nc.splitlines() if line.startswith("(G81 X"))
    assert math.isclose(plan["operations"][1]["retract_plane_z_mm"],
                        float(re.search(r"\bR(-?[\d.]+)", g81).group(1)), abs_tol=.001)
    assert math.isclose(plan["operations"][1]["drill_end_z_mm"],
                        float(re.search(r"\bZ(-?[\d.]+)", g81).group(1)), abs_tol=.001)
    assert json.loads((output / "process_plan.json").read_text())["source"] == plan["source"]


def test_public_sample_step_pair_has_no_gouge(tmp_path):
    """The chain's package path on the public sample order (examples/sample-order): runs anywhere."""
    from wheelcam.machining_step import export
    root = Path(__file__).resolve().parents[1]
    sample = root / "examples/sample-order"
    order = json.loads((sample / "spec.json").read_text())
    recipe = json.loads((sample / "sample_truth.json").read_text())["recipe"]
    report = export(recipe, tmp_path / "machining", order["hole_form"], order["spec"]["et_mm"])
    assert all(c["pass"] is not False for c in report["checks"].values())
    plan = create_step_package(tmp_path / "machining", tmp_path / "package", spec_path=sample / "spec.json")
    assert plan["simulation"]["status"] == "sampled_no_gouge"
    assert plan["simulation"]["gouge_vs_machining_step_mm3"] < 1
    assert plan["simulation"]["remaining_vs_machining_step_mm3"] > 0
    nc = (tmp_path / "package/reference.nc").read_text()
    assert nc.count("(G81 X") == order["spec"]["bolts"]
    assert all(line.startswith("(") and line.endswith(")") for line in nc.splitlines())
    assert plan["status"] == "not_released"


def test_missing_step_pair_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError, match="Missing machining STEP inputs"):
        create_step_package(tmp_path, tmp_path / "out")


def test_21_inch_text_step_refines_mesh_without_relaxing_containment(tmp_path):
    root = Path(__file__).resolve().parents[1]
    case = root / 'runs/competition-eval-20260928/workbench/text-aca0f0c9eb51/machining'
    if not (case / 'machining.step').is_file():
        pytest.skip('Local Spark 21-inch STEP regression artifact unavailable')
    plan = create_step_package(case, tmp_path / 'refined')
    geometry = plan['geometry']
    assert geometry['tessellation']['refined']
    assert geometry['tessellation']['exact_outside_mm3'] < 1
    assert plan['simulation']['step_pair_mesh_baseline_mismatch_mm3'] < geometry['machining_mesh_volume_mm3'] * .001
    assert plan['status'] == 'not_released'
