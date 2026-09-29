"""The review package must keep order dimensions and leave NC disabled."""
import pytest
import numpy as np
import json
from pathlib import Path
from types import SimpleNamespace

from wheelcam.forged_blank import ForgedWheel, face_z, recipe_from_dict, z_back
from wheelcam.manufacturing_demo import _reference_nc, confirmed_recipe, create_package, raster_sweeps, window_grid


def test_confirmed_hole_form_replaces_stale_template_without_mutating_source():
    source = {"pcd": 112.0, "bolts": 5, "center_bore_r": 33.3,
              "bolt_d": 22.0, "seat_d": 30.0, "family": "single"}
    order = {"spec": {"pcd_mm": 112.0, "bolts": 5, "center_bore_mm": 66.6},
             "hole_form": "15X32X60"}
    result = confirmed_recipe(source, order)
    assert (result["bolt_d"], result["seat_d"], result["seat_cone_deg"]) == (15, 32, 60)
    assert source["bolt_d"] == 22.0
    with pytest.raises(ValueError, match="pcd"):
        confirmed_recipe(source, {**order, "spec": {**order["spec"], "pcd_mm": 114.3}})


def test_sampled_sweep_stays_inside_target():
    target = np.zeros((90, 90), dtype=bool)
    target[15:75, 20:70] = True
    segments, swept = raster_sweeps(target)
    assert segments
    assert not np.any(swept & ~target)
    assert np.any(target & ~swept)


def test_reference_nc_is_complete_but_has_no_executable_motion():
    p = ForgedWheel()
    nc, layers = _reference_nc(p, np.arange(-6, 7, 1.5),
                               [(2, 1, 4), (3, 2, 5)], 6, -6)
    assert layers == 2
    assert nc.count("; FULL SAMPLED RASTER") == 2
    assert nc.count("(G81 X") == ForgedWheel().bolts
    assert nc.count("(G01 X") >= layers * 2 * 2
    drill = next(line for line in nc.splitlines() if line.startswith("(G81 X"))
    assert f"R{face_z(p, p.pcd / 2) + 5:.3f}" in drill
    assert f"Z{z_back(p, p.hub_r) - 3:.3f}" in drill
    assert all(line.startswith("(") and line.endswith(")") for line in nc.splitlines())


def test_window_grid_clips_outer_rim_region(monkeypatch):
    monkeypatch.setattr("wheelcam.manufacturing_demo.outlines",
                        lambda p: [[(-45, -45), (45, -45), (45, 45), (-45, 45)]])
    axis, target = window_grid(SimpleNamespace(lip_r=50, window_r_out=30), grid_mm=1)
    center = int(np.where(axis == 0)[0][0])
    assert target[center, center]
    assert target[center, center + 25]
    assert not target[center, center + 35]


def test_sample_sampled_stock_removal_has_no_design_gouge(tmp_path):
    """Legacy mesh-only package (scripts/build_manufacturing_demo.py) on the public sample order.
    The demo chain uses the STEP-pair package instead (tests/test_manufacturing_step_demo.py)."""
    sample = Path(__file__).resolve().parents[1] / "examples/sample-order"
    recipe = json.loads((sample / "sample_truth.json").read_text())["recipe"]
    spec = json.loads((sample / "spec.json").read_text())
    p = recipe_from_dict(confirmed_recipe(recipe, spec))
    from wheelcam.mesh_build import build
    design, _ = build(p)
    plan = create_package(recipe, spec, tmp_path / "m59", design_body=design)
    assert plan["simulation_3d"]["gouge_vs_design_mm3"] < 1
    assert plan["simulation_3d"]["status"] == "sampled_no_gouge"
    assert plan["simulation_3d"]["remaining_vs_design_mm3"] > 0
