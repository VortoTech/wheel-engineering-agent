"""Wheel Engineering Skill: specs -> envelope, photo -> provenance and questions, checks -> readiness."""
import json
import math
from pathlib import Path
from dataclasses import asdict, replace

import cadquery as cq
import numpy as np
import pytest

from test_forged_photo import RECIPES, oblique_camera, synthetic_photo
from wheelcam.forged_blank import ForgedWheel, recipe_from_dict, window_outlines
from wheelcam.wheel_skill import KEY_SPECS, envelope_from_specs, readiness, reconstruct, verify
from wheelcam.wheel_skill_contract import WheelInputSpec, input_evidence, understanding, validate_spec_evidence

SPEC = {"diameter_in": 20, "width_in": 9.5, "pcd_mm": 139.7, "bolts": 6, "center_bore_mm": 106.1, "et_mm": 30}


def test_envelope_from_specs_tags_every_value_with_its_source():
    upd, prov = envelope_from_specs(SPEC)
    assert upd["lip_r"] == 20 * 25.4 / 2 + 19.9 and upd["width"] == round(9.5 * 25.4 + 27.3, 1)
    assert upd["pcd"] == 139.7 and upd["bolts"] == 6 and upd["center_bore_r"] == 53.05
    assert prov["lip_r"]["source"] == "estimate" and prov["hub_r"]["source"] == "estimate"
    upd, prov = envelope_from_specs({})
    assert set(upd) == {"seat_d"} and prov["lip_r"]["source"] == "default" and prov["width"]["source"] == "default"


def test_envelope_calibration_is_labeled_same_cohort():
    root = Path(__file__).resolve().parents[1] / "runs/real-orders"
    truth_files = sorted(root.glob("case-*/*/truth.json"))
    if len(truth_files) != 13:
        pytest.skip("13 private calibration CAD truth files unavailable")
    old_width_errors, new_width_errors = [], []
    for truth_file in truth_files:
        truth = json.loads(truth_file.read_text())
        spec = json.loads((truth_file.parent / "spec.json").read_text())["spec"]
        upd, prov = envelope_from_specs(spec)
        assert 2 * upd["lip_r"] == pytest.approx(truth["lip_od_mm"], abs=.01)
        old_width_errors.append(abs(spec["width_in"] * 25.4 + 25 - truth["overall_width_mm"]))
        new_width_errors.append(abs(upd["width"] - truth["overall_width_mm"]))
        assert "同批 13 个 CAD" in prov["width"]["note"]
    assert np.mean(new_width_errors) < np.mean(old_width_errors)


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
    u = understanding(spec, prov, unknown, questions, recipe)
    assert u["evidence_groups"]["observed"]["spokes"]["value"] == truth.spokes
    assert u["evidence_groups"]["supplied"]["pcd_mm"]["value"] == 139.7
    assert u["evidence_groups"]["unknown"]["center_bore_mm"]["value"] is None
    assert u["constraint_graph"][0]["value_mm"] == pytest.approx(69.85)
    assert u["plan_status"].startswith("descriptive")
    assert u["planning_decision"]["next_action"] == "request_measurement"
    assert understanding(SPEC, prov, unknown, questions, recipe)["planning_decision"]["next_action"] == "confirm_specification"


def test_skill_input_spec_rejects_impossible_or_untyped_dimensions(tmp_path):
    assert WheelInputSpec.model_validate({}).model_dump(exclude_none=True) == {}
    with pytest.raises(ValueError, match="中心孔"):
        WheelInputSpec.model_validate({"pcd_mm": 112, "center_bore_mm": 120})
    with pytest.raises(ValueError):
        WheelInputSpec.model_validate({"diameter_in": float("nan")})
    with pytest.raises(ValueError):
        WheelInputSpec.model_validate({"unknown_key": 42})
    photo = tmp_path / "front.jpg"
    photo.write_bytes(b"reference")
    assert len(input_evidence(photo)["front"]["sha256"]) == 64
    assert validate_spec_evidence({"pcd_mm": 112}, {"pcd_mm": {"source": "drawing", "reference": "D-01"}})["pcd_mm"]["reference"] == "D-01"
    with pytest.raises(ValueError, match="未提供"):
        validate_spec_evidence({"pcd_mm": 112}, {"et_mm": {"source": "user"}})


def test_reconstruct_rejects_an_oblique_front(front_photo):
    truth, _ = front_photo
    project, _ = oblique_camera(truth, tilt_deg=35)
    with pytest.raises(ValueError, match="正视图"):
        reconstruct(synthetic_photo(truth, project, window_outlines(truth, samples=96)), SPEC)


def test_front_circle_ignores_corner_labels_and_floor_shadow():
    from wheelcam.wheel_skill import _front_rim_hub

    yy, xx = np.mgrid[:512, :512]
    radius = np.hypot(xx - 256, yy - 256)
    image = np.ones((512, 512, 3), float)
    image[(radius > 165) & (radius < 180)] = .15
    image[12:58, 5:125] = .2  # specification block outside the wheel
    image[425:435, :512] = .5  # floor shadow next to the wheel's bottom
    rim, hub = _front_rim_hub(image)
    assert hub == pytest.approx([256, 256], abs=4)
    assert np.linalg.norm(np.asarray(rim[0]) - hub) == pytest.approx(180, abs=5)


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
                                      "bolt_pattern", "no_sliver_faces", "rotational_symmetry", "step_roundtrip")}
    confirmed = {k: {"source": "user"} for k in SPEC}
    assert readiness({}, {}, SPEC, built=False)[0] == "L0"
    level, why = readiness({}, ok, SPEC, built=True, kernel="mesh")
    assert level == "L0" and any("GLB" in w for w in why)
    assert readiness({}, {k: v for k, v in ok.items() if k != "step_roundtrip"}, SPEC, built=True)[0] == "L0"
    assert readiness({}, ok, SPEC, built=True)[0] == "L1"
    assert readiness({}, ok, SPEC, built=True,
                     spec_evidence={**confirmed, "pcd_mm": {"source": "catalog"}})[0] == "L1"
    assert readiness({}, ok, {"diameter_in": 20}, built=True)[0] == "L1"
    assert readiness({}, {**ok, "no_sliver_faces": {"pass": False}}, SPEC, built=True,
                     spec_evidence=confirmed)[0] == "L2"
    level, why = readiness({}, ok, SPEC, built=True, spec_evidence=confirmed)
    assert level == "L3" and any("不可直接用于制造" in w for w in why)
    assert readiness({}, {**ok, "bolt_pattern": {"pass": False}}, SPEC, built=True)[0] == "L1"
    assert readiness({}, {k: v for k, v in ok.items() if k != "offset_et"}, SPEC, built=True,
                     spec_evidence=confirmed)[0] == "L1"
    assert readiness({}, {k: v for k, v in ok.items() if k != "no_sliver_faces"}, SPEC, built=True,
                     spec_evidence=confirmed)[0] == "L2"
    assert set(KEY_SPECS) == set(SPEC)


def test_skipped_style_and_failed_preparation_prevent_l3(tmp_path):
    path, p = plate(tmp_path)
    report = {"forged": {"skipped_operations": ["spoke_grooves"]},
              "preparation": {"status": "failed", "error": "sample failure"}}
    checks = verify(path, p, {}, report)
    assert checks["all_style_stages_built"] == {"pass": False, "skipped_operations": ["spoke_grooves"]}
    assert checks["preparation_execution"]["pass"] is False
    passed = {k: {"pass": True} for k in ("single_valid_solid", "step_roundtrip", "outer_diameter",
                                        "overall_width", "offset_et", "bolt_pattern", "no_sliver_faces",
                                        "rotational_symmetry")}
    confirmed = {k: {"source": "drawing"} for k in SPEC}
    level, limits = readiness({}, {**passed, **checks}, SPEC, True,
                              spec_evidence=confirmed, skipped_operations=["spoke_grooves"])
    assert level == "L2" and any("spoke_grooves" in line for line in limits)
    assert any("preparation_execution" in line for line in limits)


def test_hole_form_is_saved_with_drawing_provenance(tmp_path, front_photo):
    from PIL import Image
    from wheelcam.wheel_skill import run

    _, photo = front_photo
    front = tmp_path / "front.png"
    Image.fromarray((photo * 255).astype("uint8")).save(front)
    result = run(front, SPEC, tmp_path / "output", build=False, hole_form="15X32X60")
    recipe = json.loads((tmp_path / "output/recipe.json").read_text())
    assert (recipe["bolt_d"], recipe["seat_d"], recipe["seat_cone_deg"]) == (15, 32, 60)
    assert all(result["parameters"][key]["source"] == "drawing"
               for key in ("bolt_d", "seat_d", "seat_cone_deg"))
    assert result["engineering_understanding"]["evidence_groups"]["supplied"]["bolt_d"]["value"] == 15
    with pytest.raises(ValueError, match="无法解析"):
        run(front, SPEC, tmp_path / "invalid", build=False, hole_form="wrong")


def test_style_agent_records_visual_score_without_changing_engineering_dimensions(tmp_path, front_photo, monkeypatch):
    from PIL import Image
    from wheelcam import style_agent as style_module
    from wheelcam.wheel_skill import run

    _, photo = front_photo
    front = tmp_path / "front.png"
    Image.fromarray((photo * 255).astype("uint8")).save(front)
    monkeypatch.setenv("WHEELCAM_VLM_BASE_URL", "http://local-vlm.invalid/v1")

    def fake_agent(recipe, path, out, spec):
        assert path == front and spec == SPEC and out == tmp_path / "corrected/style"
        after = {**recipe, "flank_w": 4.0}
        return after, {"changed": {"flank_w": {"from": recipe["flank_w"], "to": 4.0}},
                       "steps": [{"step": "search", "scores": [{"flank_w": 16.0, "edge_mm": 4.0},
                                                                 {"flank_w": 4.0, "edge_mm": 2.1}],
                                  "chosen": {"flank_w": 4.0, "edge_mm": 2.1}}]}

    monkeypatch.setattr(style_module, "run", fake_agent)
    result = run(front, SPEC, tmp_path / "corrected", build=False, style_agent=True)
    saved = json.loads((tmp_path / "corrected/engineering_report.json").read_text())
    recipe = json.loads((tmp_path / "corrected/recipe.json").read_text())
    assert result["style_agent"] == saved["style_agent"] == {"flank_w": {"from": 16.0, "to": 4.0}}
    assert result["style_agent_status"] == "applied" and result["readiness"] == "L0"
    assert result["parameters"]["flank_w"]["source"] == "agent"
    assert result["parameters"]["flank_w"]["evidence"]["chosen"]["edge_mm"] == 2.1
    assert recipe["flank_w"] == 4.0 and recipe["pcd"] == SPEC["pcd_mm"]


def test_style_agent_accepts_lip_windows_and_their_derived_radial_band(tmp_path, front_photo, monkeypatch):
    from PIL import Image
    from wheelcam import style_agent as style_module
    from wheelcam.recipe_chat import apply_edit, review
    from wheelcam.wheel_skill import run

    _, photo = front_photo
    front = tmp_path / "front.png"
    Image.fromarray((photo * 255).astype("uint8")).save(front)
    monkeypatch.setenv("WHEELCAM_VLM_BASE_URL", "http://local-vlm.invalid/v1")

    def agent_with_lip_windows(recipe, *_):
        decision = review(recipe, {"changes": [{"param": "lip_pockets", "value": 18}]})
        assert decision["accepted"] == {"lip_pockets": 18.0}
        edited = apply_edit(recipe, decision["accepted"])
        changed = {k: {"from": recipe[k], "to": edited[k]} for k in recipe if recipe[k] != edited[k]}
        return edited, {"changed": changed, "steps": [
            {"step": "look", "question": "照片外圈是否有一圈小盲窗", "answer": True,
             "p_true": .9, "count": {"value": 18, "method": "角向周期"}},
            {"step": "verify", "accepted": decision["accepted"], "checks_failed": [], "result": "已采用"}]}

    monkeypatch.setattr(style_module, "run", agent_with_lip_windows)
    result = run(front, SPEC, tmp_path / "lip", build=False, style_agent=True)
    recipe = json.loads((tmp_path / "lip/recipe.json").read_text())
    assert result["style_agent_status"] == "applied"
    assert set(result["style_agent"]) == {"lip_pockets", "lip_pocket_r"}
    assert recipe["lip_pockets"] == 18
    assert recipe["lip_pocket_r"] == [recipe["ring_r"] + 2, recipe["lip_face_r_in"] - 3]
    assert result["parameters"]["lip_pocket_r"]["source"] == "agent"
    assert result["parameters"]["lip_pocket_r"]["evidence"]["step"] == "look"
    assert recipe["pcd"] == SPEC["pcd_mm"] and result["readiness"] == "L0"


def test_style_agent_falls_back_without_config_or_on_failure(tmp_path, front_photo, monkeypatch):
    from PIL import Image
    from wheelcam import style_agent as style_module
    from wheelcam.wheel_skill import run

    _, photo = front_photo
    front = tmp_path / "front.png"
    Image.fromarray((photo * 255).astype("uint8")).save(front)
    monkeypatch.delenv("WHEELCAM_VLM_BASE_URL", raising=False)
    unconfigured = run(front, SPEC, tmp_path / "unconfigured", build=False, style_agent=True)
    assert unconfigured["style_agent_status"] == "not_configured"
    assert any("WHEELCAM_VLM_BASE_URL" in q for q in unconfigured["questions"])

    monkeypatch.setenv("WHEELCAM_VLM_BASE_URL", "http://local-vlm.invalid/v1")
    def failing_agent(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(style_module, "run", failing_agent)
    failed = run(front, SPEC, tmp_path / "failed", build=False, style_agent=True)
    assert failed["style_agent_status"] == "failed" and failed["style_agent"] == {}
    assert any("保留预设造型" in q for q in failed["questions"])
    assert json.loads((tmp_path / "failed/recipe.json").read_text()) == json.loads(
        (tmp_path / "unconfigured/recipe.json").read_text())

    def illegal_agent(recipe, *args):
        return {**recipe, "pcd": recipe["pcd"] + 1}, {
            "changed": {"pcd": {"from": recipe["pcd"], "to": recipe["pcd"] + 1}}, "steps": []}
    monkeypatch.setattr(style_module, "run", illegal_agent)
    rejected = run(front, SPEC, tmp_path / "rejected", build=False, style_agent=True)
    assert rejected["style_agent_status"] == "failed"
    assert json.loads((tmp_path / "rejected/recipe.json").read_text())["pcd"] == SPEC["pcd_mm"]


def test_run_rejects_nonempty_output_before_touching_inputs(tmp_path):
    from wheelcam.wheel_skill import run
    out = tmp_path / "old-run"
    out.mkdir()
    (out / "cad").mkdir()
    (out / "cad" / "wheel.step").write_text("old STEP")
    with pytest.raises(ValueError, match="输出目录非空"):
        run(tmp_path / "missing.jpg", SPEC, out)
    assert (out / "cad" / "wheel.step").read_text() == "old STEP"


def test_skill_report_keeps_photo_candidates_and_missing_specs_separate(tmp_path, front_photo, monkeypatch):
    from PIL import Image
    from wheelcam.wheel_skill import run

    _, photo = front_photo
    front = tmp_path / "front.png"
    Image.fromarray((photo * 255).astype("uint8")).save(front)
    monkeypatch.setenv("WHEELCAM_VLM_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("WHEELCAM_VLM_MODEL", "unavailable")
    result = run(front, {"diameter_in": 20, "pcd_mm": 139.7}, tmp_path / "output", build=False)
    saved = json.loads((tmp_path / "output" / "engineering_report.json").read_text())

    assert result["readiness"] == "L0" and saved["manufacturing_status"] == "not_released"
    assert saved["engineering_understanding"]["evidence_groups"]["supplied"]["pcd_mm"]["value"] == 139.7
    assert saved["engineering_understanding"]["evidence_groups"]["supplied"]["pcd_mm"]["source"] == "unspecified"
    assert saved["engineering_understanding"]["evidence_groups"]["unknown"]["et_mm"]["value"] is None
    assert saved["input_evidence"]["front"]["sha256"] == input_evidence(front)["front"]["sha256"]
    assert saved["output_evidence"]["recipe.json"]["bytes"] > 0
    assert saved["artifacts"] == {}


def test_mesh_visual_comparison_stays_l0_and_saves_overlay(tmp_path, front_photo, monkeypatch):
    from PIL import Image
    from wheelcam import mesh_build, visual_check as visual_tools
    from wheelcam.wheel_skill import run

    _, photo = front_photo
    front = tmp_path / "front.png"
    Image.fromarray((photo * 255).astype("uint8")).save(front)
    built_holes = []
    def fake_build(recipe):
        built_holes.append((recipe.bolt_d, recipe.seat_d, recipe.seat_cone_deg))
        return object(), {"mass_kg_6061": 1.0}
    monkeypatch.setattr(mesh_build, "build", fake_build)
    monkeypatch.setattr(mesh_build, "export_glb", lambda body, path: path.write_bytes(b"preview"))
    monkeypatch.setattr(mesh_build, "verify", lambda body, recipe, spec: {"single_valid_solid": {"pass": True}})
    monkeypatch.setattr(visual_tools, "compare_front", lambda *args: ({"window_iou": 0.9}, np.zeros((8, 8, 3), dtype="uint8")))

    result = run(front, SPEC, tmp_path / "visual", kernel="mesh", visual_check=True,
                 hole_form="15X32X60")
    assert result["readiness"] == "L0"
    assert built_holes == [(15, 32, 60)]
    assert result["visual_check"]["front"]["window_iou"] == 0.9
    assert (tmp_path / "visual" / "cad" / "front_overlay.png").exists()
    assert "cad/front_overlay.png" in result["output_evidence"]


def test_lug_seat_is_kept_out_of_the_centre_bore():
    """6 x 139.7 / CB 106.1: a 30 mm seat would cut into the bore; it is shrunk and asked about."""
    from wheelcam.wheel_skill import BORE_WALL, SEAT_D
    upd, prov = envelope_from_specs(SPEC)
    assert SPEC["pcd_mm"] / 2 - upd["seat_d"] / 2 - upd["center_bore_r"] >= BORE_WALL
    assert prov["seat_d"]["source"] == "estimate"
    upd, prov = envelope_from_specs({**SPEC, "pcd_mm": 165.1, "center_bore_mm": 78.1})   # plenty of room
    assert upd["seat_d"] == SEAT_D and prov["seat_d"]["source"] == "estimate"      # not the template's 40 (HF6-5)


def test_style_features_come_from_rules_and_are_asked_about(front_photo):
    """Style features: forged_y preset + question by default; a user/VLM answer switches them without a question."""
    from wheelcam.wheel_skill import style_features
    truth, photo = front_photo
    recipe, prov, questions, _, _ = reconstruct(photo, SPEC)
    assert recipe.face_chamfer > 0 and recipe.hub_crease_r > recipe.hub_r
    assert prov["center_pad"]["source"] == "default" and prov["center_pad"]["value"] is True
    assert recipe.flank_w == 16 and any("锻造 Y 辐" in q for q in questions)
    base = {**asdict(recipe)}
    upd, prov, questions = style_features(base, {"center_pad": False, "arm_groove": False, "hub_valleys": False,
                                                 "deep_flank": False, "source": "user"})
    assert not any("锻造 Y 辐" in q for q in questions) and prov["center_pad"]["source"] == "user"
    assert "spoke_pad_w" not in upd and upd["hub_valley_depth"] == 0 and upd["flank_w"] == 4
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
