import json

import pytest

from test_mesh_build import outline_recipe


def fake(changes, reply="好的", refused=()):
    return lambda recipe, message, history=(): {"changes": list(changes), "reply": reply, "refused": list(refused)}


def test_review_refuses_engineering_fixes_and_clips_ranges():
    from wheelcam.recipe_chat import review
    d = review(outline_recipe(), {"changes": [{"param": "pcd", "value": 120}, {"param": "et", "value": 30},
                                              {"param": "spoke_pad_depth", "value": 40},
                                              {"param": "os.system", "value": 1}]})
    assert d["accepted"] == {"spoke_pad_depth": 16.0}
    assert {r["param"] for r in d["refused"]} == {"pcd", "web_thick_hub", "os.system"}
    assert d["notes"]


def test_deeper_dish_keeps_the_mounting_face():
    from wheelcam.forged_blank import recipe_from_dict, z_back
    from wheelcam.recipe_chat import apply_edit
    r = outline_recipe(web_thick_hub=70.0)
    p0 = recipe_from_dict(r)
    p1 = recipe_from_dict(apply_edit(r, {"hub_z": p0.hub_z - 10}))
    assert z_back(p1, p1.hub_r) == pytest.approx(z_back(p0, p0.hub_r))
    with pytest.raises(ValueError):
        apply_edit(r, {"hub_z": p0.hub_z - 60})           # the bolt seats would not fit


def test_turn_rebuilds_and_rechecks(tmp_path):
    from wheelcam.recipe_chat import turn
    r = outline_recipe()
    result = turn(r, "脊线高一点", tmp_path, ask=fake([{"param": "spoke_pad_depth", "value": 9}]))
    assert result["changed"] == {"spoke_pad_depth": {"from": 6.0, "to": 9.0}}
    assert "已修改" in result["summary"] and "检查全部通过" in result["summary"]
    assert result["built"]["checks"]["single_valid_solid"]["pass"]
    assert json.loads((tmp_path / "recipe.json").read_text())["spoke_pad_depth"] == 9
    assert (tmp_path / "wheel.glb").stat().st_size > 0
    nothing = turn(r, "改成 6 孔", tmp_path / "x", ask=fake([{"param": "bolts", "value": 6}]))
    assert nothing["built"] is None and nothing["refused"][0]["param"] == "bolts"


def test_parse_answer_strips_thinking():
    from wheelcam.recipe_chat import parse_answer
    a = parse_answer('<think>hmm</think>\n```json\n{"changes": [], "reply": "请说具体一点"}\n```')
    assert a == {"changes": [], "reply": "请说具体一点", "refused": []}


def test_hub_recess_is_refused_when_the_centre_plate_gets_too_thin():
    from wheelcam.recipe_chat import turn
    r = outline_recipe(web_thick_hub=30.0)
    result = turn(r, "中心下沉 20", None, ask=fake([{"param": "hub_recess_depth", "value": 20}]))
    assert result["built"] is None and result["refused"][0]["param"] == "hub_recess_depth"
