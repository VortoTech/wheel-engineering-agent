import json

import pytest


EXAMPLE = "20×10.5 ET15，5×112，中心孔 66.6，孔型 15X32X60。做 5 辐直辐，辐条宽一点，凹深一点，外圈加一圈 15 个盲窗。"


def test_explicit_specs_blind_windows_and_mesh_checks(tmp_path):
    from wheelcam.text_wheel import run
    proposal = {"spec": {"pcd_mm": 120}, "family": "single", "spokes": 6,
                "shape": {"stem_w_hub": 48}, "style": {"hub_z": -85, "lip_pockets": 12}}
    report = run(EXAMPLE, tmp_path / "wheel", ask=lambda _: proposal)
    assert report["all_checks_pass"] and report["build_seconds"] < 10
    assert report["spec"] == {"diameter_in": 20, "width_in": 10.5, "pcd_mm": 112,
                              "bolts": 5, "center_bore_mm": 66.6, "et_mm": 15}
    assert all(report["parameters"][k]["source"] == "user" for k in report["spec"])
    assert report["parameters"]["hole_form"] == {"value": "15X32X60", "source": "user"}
    assert report["parameters"]["spokes"]["value"] == 5
    assert report["parameters"]["lip_pockets"] == {"value": 15, "source": "user"}
    recipe = json.loads((tmp_path / "wheel" / "recipe.json").read_text())
    assert recipe["spokes"] == 5 and recipe["lip_pockets"] == 15 and recipe["bolt_d"] == 15
    assert (tmp_path / "wheel" / "wheel.glb").read_bytes()[:4] == b"glTF"
    assert (tmp_path / "wheel" / "front.png").exists()
    assert report["readiness"] == "L0" and report["manufacturing_status"] == "not_released"
    assert {r["param"] for r in report["rejected"]} >= {"pcd_mm", "spokes", "lip_pockets"}


def test_missing_spec_is_default_with_questions(tmp_path):
    from wheelcam.text_wheel import run
    report = run("做一个 6 辐 Y 形分叉的 21 寸轮毂", tmp_path / "wheel",
                 ask=lambda _: {"family": "y_split", "spokes": 6, "spec": {"et_mm": 15}})
    assert report["spec"]["diameter_in"] == 21
    assert report["parameters"]["diameter_in"]["source"] == "user"
    for key in ("width_in", "pcd_mm", "bolts", "center_bore_mm", "et_mm"):
        assert report["parameters"][key]["source"] == "default"
        assert any(key in q for q in report["questions"])
    assert report["parameters"]["spokes"] == {"value": 6, "source": "user"}
    assert report["all_checks_pass"]


@pytest.mark.parametrize("message", ["像某品牌某款那样", "给我做一个扭转辐"])
def test_unsupported_templates_are_explicitly_refused(message):
    from wheelcam.text_wheel import review
    with pytest.raises(ValueError, match="不能可靠生成"):
        review(message, {"family": "single"})


def test_model_cannot_override_explicit_pcd_or_add_unknown_fields():
    from wheelcam.text_wheel import review
    result = review("20×9J，PCD 114.3，5 辐直辐", {
        "spec": {"pcd_mm": 112, "center_bore_mm": 66.6}, "family": "single",
        "shape": {"dangerous_field": 100, "stem_w_hub": 10000},
        "style": {"pcd": 120, "flank_w": 1000}})
    assert result["spec"]["pcd_mm"] == 114.3
    assert result["parameters"]["pcd_mm"]["source"] == "user"
    assert {r["param"] for r in result["rejected"]} >= {"pcd_mm", "center_bore_mm", "dangerous_field", "pcd"}
    assert {r["param"] for r in result["clipped"]} >= {"stem_w_hub", "flank_w"}


def test_text_recipe_exports_valid_machining_step(tmp_path):
    from wheelcam.text_wheel import run
    from wheelcam.machining_step import export
    out = tmp_path / "wheel"
    report = run(EXAMPLE, out, ask=lambda _: {"family": "single", "spokes": 5, "style": {"lip_pockets": 15}})
    result = export(json.loads((out / "recipe.json").read_text()), tmp_path / "machining",
                    report["hole_form"], report["spec"]["et_mm"])
    assert (tmp_path / "machining" / "machining.step").exists()
    assert result["checks"]["valid_single_solid"]["pass"]
    assert result["checks"]["step_roundtrip"]["pass"]
