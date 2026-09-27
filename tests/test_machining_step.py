import json

import pytest

from test_mesh_build import outline_recipe


def test_machining_step_is_one_valid_solid_with_the_order_hole_form(tmp_path):
    from wheelcam.machining_step import export
    report = export(outline_recipe(), tmp_path, "15X32X60", et_mm=None)
    checks = report["checks"]
    assert checks["valid_single_solid"]["pass"] and checks["step_roundtrip"]["pass"]
    assert checks["no_sliver_faces"]["pass"]
    assert report["windows"] > 0 and report["hole_form"]["bolt_d"] == 15.0
    recipe = json.loads((tmp_path / "recipe.json").read_text())
    assert (recipe["bolt_d"], recipe["seat_d"], recipe["seat_cone_deg"]) == (15.0, 32.0, 60.0)
    assert (tmp_path / "machining.step").stat().st_size > 0


def test_window_loops_stay_inside_the_through_radius():
    import numpy as np
    from wheelcam.forged_blank import recipe_from_dict
    from wheelcam.machining_step import through_radius, window_loops
    p = recipe_from_dict(outline_recipe())
    loops = window_loops(p)
    assert loops
    assert max(float(np.hypot(*l.T).max()) for l in loops) <= through_radius(p) + .5
