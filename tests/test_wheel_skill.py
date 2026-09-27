"""Wheel Engineering Skill: specs -> envelope, photo -> provenance and questions, checks -> readiness."""
import json
import math
from dataclasses import asdict, replace

import cadquery as cq
import numpy as np
import pytest

from test_forged_photo import RECIPES, oblique_camera, synthetic_photo
from wheelcam.forged_blank import ForgedWheel, recipe_from_dict, window_outlines
from wheelcam.wheel_skill import KEY_SPECS, envelope_from_specs, readiness, reconstruct, verify

SPEC = {"diameter_in": 20, "width_in": 9.5, "pcd_mm": 139.7, "bolts": 6, "center_bore_mm": 106.1, "et_mm": 30}


def test_envelope_from_specs_tags_every_value_with_its_source():
    upd, prov = envelope_from_specs(SPEC)
    assert upd["lip_r"] == 20 * 25.4 / 2 + 18 and upd["width"] == round(9.5 * 25.4 + 25, 1)
    assert upd["pcd"] == 139.7 and upd["bolts"] == 6 and upd["center_bore_r"] == 53.05
    assert prov["lip_r"]["source"] == "spec" and prov["hub_r"]["source"] == "estimate"
    upd, prov = envelope_from_specs({})
    assert set(upd) == {"seat_d"} and prov["lip_r"]["source"] == "default" and prov["width"]["source"] == "default"


@pytest.fixture(scope="module")
def front_photo():
    """Straight-on synthetic photo of the HF6 recipe (zero tilt: no depth parallax)."""
    truth = recipe_from_dict(json.loads((RECIPES / "hf6-y-split.json").read_text()))
    project, _ = oblique_camera(truth, tilt_deg=0)
    return truth, synthetic_photo(truth, project, window_outlines(truth, samples=160))


def test_reconstruct_measures_the_front_and_asks_for_the_rest(front_photo):
    truth, photo = front_photo
    spec = {k: v for k, v in SPEC.items() if k != "center_bore_mm"}
    recipe, prov, questions, unknown, evidence = reconstruct(photo, spec)
    assert recipe.spokes == truth.spokes and prov["spokes"]["source"] == "photo"
    assert evidence["trace"]["windows_per_group"] == 2
    assert recipe.family == "outline" and recipe.bolts == 6 and recipe.pcd == 139.7
    assert any("center_bore_mm" in q for q in questions)           # a missing spec becomes a question
    assert any("斜视图" in q for q in questions)                     # no oblique photo: dish depth is a default
    assert prov["dish_depth"]["source"] in ("default", "rule")       # rule: the template dish broke the web range
    assert recipe.hub_z - recipe.web_thick_hub + recipe.width / 2 == pytest.approx(30, abs=.1)   # ET kept
    assert set(unknown) >= {"back_side", "wall_thickness"}


def test_reconstruct_rejects_an_oblique_front(front_photo):
    truth, _ = front_photo
    project, _ = oblique_camera(truth, tilt_deg=35)
    with pytest.raises(ValueError, match="正视图"):
        reconstruct(synthetic_photo(truth, project, window_outlines(truth, samples=96)), SPEC)


def plate(tmp_path, bolts=6, p=None, extra=()):
    """A holed disc standing in for a built wheel: cheap to verify, same checks."""
    p = p or replace(ForgedWheel(), lip_r=200.0, width=40.0, pcd=139.7, bolts=6, spokes=6)
    part = cq.Workplane("XY").circle(p.lip_r).extrude(p.width)
    pts = [(p.pcd / 2 * math.cos(2 * math.pi * k / bolts), p.pcd / 2 * math.sin(2 * math.pi * k / bolts)) for k in range(bolts)]
    part = part.faces(">Z").workplane().pushPoints(pts).hole(p.bolt_d)
    if extra:
        part = part.faces(">Z").workplane().pushPoints(list(extra)).hole(30)
    path = tmp_path / f"plate{bolts}_{len(extra)}.step"
    cq.exporters.export(part, str(path))
    return path, p


def test_verify_checks_dimensions_and_bolt_pattern(tmp_path):
    path, p = plate(tmp_path)
    checks = verify(path, p, {"diameter_in": 1, "width_in": 1}, {})
    assert all(c["pass"] is True for c in checks.values()), checks
    assert checks["bolt_pattern"]["holes_found"] == 6
    path, _ = plate(tmp_path, bolts=5)
    assert verify(path, p, {}, {})["bolt_pattern"]["pass"] is False
    path, _ = plate(tmp_path, extra=[(150, 0)])                    # one sector differs from the others
    checks = verify(path, p, {}, {})
    assert checks["bolt_pattern"]["pass"] is True and checks["rotational_symmetry"]["pass"] is False


def test_readiness_never_claims_more_than_the_evidence():
    ok = {k: {"pass": True} for k in ("single_valid_solid", "outer_diameter", "overall_width", "offset_et",
                                      "bolt_pattern", "no_sliver_faces", "rotational_symmetry")}
    assert readiness({}, {}, SPEC, built=False)[0] == "L0"
    assert readiness({}, ok, {"diameter_in": 20}, built=True)[0] == "L1"
    assert readiness({}, {**ok, "no_sliver_faces": {"pass": False}}, SPEC, built=True)[0] == "L2"
    level, why = readiness({}, ok, SPEC, built=True)
    assert level == "L3" and any("L4/L5" in w for w in why)
    assert readiness({}, {**ok, "bolt_pattern": {"pass": False}}, SPEC, built=True)[0] == "L1"
    assert set(KEY_SPECS) == set(SPEC)


def test_lug_seat_is_kept_out_of_the_centre_bore():
    """6 x 139.7 / CB 106.1: a 30 mm seat would cut into the bore; it is shrunk and asked about."""
    from wheelcam.wheel_skill import BORE_WALL, SEAT_D
    upd, prov = envelope_from_specs(SPEC)
    assert SPEC["pcd_mm"] / 2 - upd["seat_d"] / 2 - upd["center_bore_r"] >= BORE_WALL
    assert prov["seat_d"]["source"] == "estimate"
    upd, prov = envelope_from_specs({**SPEC, "pcd_mm": 165.1, "center_bore_mm": 78.1})   # plenty of room
    assert upd["seat_d"] == SEAT_D and prov["seat_d"]["source"] == "estimate"      # not the template's 40 (HF6-5)


def test_style_features_come_from_rules_and_are_asked_about(front_photo):
    """Style features: preset + question by default; a VLM/user answer switches them without a question."""
    from wheelcam.wheel_skill import style_features
    truth, photo = front_photo
    recipe, prov, questions, _, _ = reconstruct(photo, SPEC)
    assert recipe.face_chamfer > 0 and recipe.hub_crease_r > recipe.hub_r
    assert prov["center_pad"]["source"] == "default" and any("锻造 Y 辐" in q for q in questions)
    base = {**asdict(recipe)}
    upd, prov, questions = style_features(base, {"center_pad": False, "arm_groove": False, "hub_valleys": False, "source": "vlm"})
    assert not questions and prov["center_pad"]["source"] == "vlm"
    assert "spoke_pad_w" not in upd and "outline_groove_r" not in upd and upd["hub_valley_depth"] == 0
    plain, _, questions, _, _ = reconstruct(photo, SPEC, style=False)
    assert plain.spoke_pad_w == 0 and not any("锻造 Y 辐" in q for q in questions)


def test_hub_web_overrules_a_photo_dish_outside_forging_practice():
    """HF6-5: one camera gave a 23.5 mm web, another 85 mm; the web is held in WEB_HUB, ET kept."""
    from wheelcam.wheel_skill import RING_Z_MAX, WEB_HUB, _hold_hub_web
    for web, hub_z, ring_z in ((85.0, -29.5, 0.0), (23.5, -91.0, -14.0)):
        r = {"hub_z": hub_z, "ring_z": ring_z, "web_thick_hub": web}
        mount = hub_z - web
        assert _hold_hub_web(r)
        assert WEB_HUB[0] <= r["web_thick_hub"] <= WEB_HUB[1]
        assert r["hub_z"] - r["web_thick_hub"] == pytest.approx(mount)          # mounting face (ET) unchanged
        assert r["hub_z"] < r["ring_z"] <= RING_Z_MAX
    r = {"hub_z": -58.0, "ring_z": -20.0, "web_thick_hub": 45.2}               # HF6-4: left alone
    assert _hold_hub_web(r) is None and r["hub_z"] == -58.0
