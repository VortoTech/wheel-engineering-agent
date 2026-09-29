import json

import pytest


@pytest.fixture(autouse=True)
def isolated_style_library(monkeypatch, tmp_path):
    monkeypatch.setenv('WHEELCAM_STYLE_LIBRARY', str(tmp_path / 'missing-catalog.json'))


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


def test_chinese_adjacent_dimensions_and_hole_form_are_not_lost():
    from wheelcam.text_wheel import specs_in_text, review
    message = "做20×10.5J轮毂，偏距15，孔距5×112，中心孔66.6，孔型15X32X60。"
    spec, form = specs_in_text(message)
    assert spec == {"diameter_in": 20, "width_in": 10.5, "et_mm": 15,
                    "bolts": 5, "pcd_mm": 112, "center_bore_mm": 66.6}
    assert form == "15X32X60"
    decision = review("做一个21寸的6辐Y形轮毂", {"family": "y_split", "spokes": 6})
    assert decision["parameters"]["diameter_in"] == {"value": 21, "source": "user"}
    assert "width_in" in decision["unknown"]


def test_hole_form_cannot_be_misread_as_wheel_dimensions():
    from wheelcam.text_wheel import specs_in_text
    spec, form = specs_in_text("只知道孔型 15X32X60，轮毂尺寸还没量")
    assert spec == {} and form == "15X32X60"


def synthetic_catalog(tmp_path, monkeypatch, spokes=5):
    from wheelcam.text_wheel import review
    d = review(f'20寸{spokes}辐直辐', {})['recipe']
    entry = {'id': 'style-001', 'description': f'{spokes} 辐直辐', 'spokes': spokes, 'family': 'single',
             'outlines': [[[r / d['lip_r'], a] for r, a in loop] for loop in d['outlines']],
             'style': {'flank_w': 4, 'flank_depth': 6, 'hub_z': -80, 'spoke_pad_depth': 10,
                       'pcd': 999, 'bolts': 9, 'width': 999},
             'radial': {'window_r_out': d['window_r_out'] / d['lip_r'], 'pcd': 999}}
    p = tmp_path / 'catalog.json'
    p.write_text(json.dumps({'entries': [entry]}))
    monkeypatch.setenv('WHEELCAM_STYLE_LIBRARY', str(p))
    return entry


def test_private_template_scales_only_style_and_preserves_text_specs(tmp_path, monkeypatch):
    from wheelcam.text_wheel import review
    e = synthetic_catalog(tmp_path, monkeypatch)
    d = review('21寸5辐直辐，孔距5×114.3', {'style_id': e['id']})
    assert d['style_template']['kind'] == 'private_outline'
    assert d['recipe']['pcd'] == 114.3 and d['recipe']['bolts'] == 5
    assert d['recipe']['width'] != 999
    assert d['recipe']['outlines'][0][0][0] == pytest.approx(e['outlines'][0][0][0] * d['recipe']['lip_r'])
    assert d['parameters']['flank_w']['source'] == 'default'
    mismatch = review('6辐直辐', {'style_id': e['id']})
    assert mismatch['style_template']['kind'] == 'parametric'
    assert '参数模板，造型较简化' in mismatch['reply']


def test_missing_and_invalid_style_values_keep_nonzero_defaults():
    from wheelcam.text_wheel import review
    d = review('20寸5辐直辐', {'shape': {'stem_w_hub': ''},
                            'style': {'flank_w': '', 'flank_depth': 'Y形分叉'}})
    assert d['recipe']['flank_w'] == 4 and d['recipe']['flank_depth'] == 6
    assert d['parameters']['flank_w']['source'] == 'default'
    assert {r['param'] for r in d['rejected']} == {'flank_depth'}
    assert d['recipe']['web_thick_hub'] >= 32


@pytest.mark.parametrize('spokes', [5, 6])
def test_ring_of_pockets_has_separate_components(tmp_path, monkeypatch, spokes):
    from wheelcam.text_wheel import run
    synthetic_catalog(tmp_path, monkeypatch, spokes)
    r = run(f'20寸{spokes}辐轮毂，外圈加一圈盲窗', tmp_path / 'result', ask=lambda _: {'style': {'lip_pockets': ''}})
    assert r['all_checks_pass']
    assert r['checks']['independent_lip_pockets']['tool_components'] == spokes * 3
    assert r['parameters']['lip_pockets'] == {'value': spokes * 3, 'source': 'default'}
    assert any('盲窗数量' in q for q in r['questions'])
    assert any('et_mm' in q for q in r['questions'])


@pytest.mark.parametrize("spokes", [5, 7, 9])
def test_parametric_boundary_window_not_duplicated(spokes):
    from wheelcam.text_wheel import review
    from wheelcam.mesh_build import lip_window_tools
    from wheelcam.forged_blank import recipe_from_dict
    d = review(f'20寸{spokes}辐直辐，外圈{spokes * 3}个盲窗', {})
    tools = lip_window_tools(recipe_from_dict(d['recipe']))
    assert len(d['recipe']['outlines']) == 1
    assert len(tools) == len(sum(tools[1:], tools[0]).decompose()) == spokes * 3


def test_library_generator_strips_order_data_and_limits_output(tmp_path, monkeypatch):
    from wheelcam import text_style_library as lib
    monkeypatch.setattr(lib, 'ROOT', tmp_path)
    source = tmp_path / 'source/private-order/build'
    source.mkdir(parents=True)
    (source / 'recipe.json').write_text(json.dumps({'family': 'outline', 'spokes': 5, 'lip_r': 250,
       'outlines': [[[100, 10], [200, 20], [100, 30]]], 'pcd': 999, 'customer': 'secret-client',
       'width': 777, 'flank_w': 4}))
    output = tmp_path / 'runs/style-library/catalog.json'
    assert lib.generate(tmp_path / 'source', output) == 1
    content = output.read_text()
    assert not any(x in content for x in ['secret-client', 'private-order', 'pcd', 'width', '250'])
    with pytest.raises(ValueError, match='runs'):
        lib.generate(tmp_path / 'source', tmp_path / 'public.json')


@pytest.mark.parametrize('phrase', ['外圈不要盲窗', '外圈不加盲窗', '无盲窗', '去掉外圈盲窗', '外圈盲窗取消'])
def test_negative_pockets_override_model(phrase):
    from wheelcam.text_wheel import review
    d = review('20寸5辐直辐，' + phrase, {'style': {'lip_pockets': 15}})
    assert d['recipe']['lip_pockets'] == 0
    assert d['parameters']['lip_pockets']['source'] == 'user'
    assert not any('确认盲窗数量' in q for q in d['questions'])


def test_thin_template_repaired_without_moving_et(tmp_path, monkeypatch):
    from wheelcam.text_wheel import review
    synthetic_catalog(tmp_path, monkeypatch)
    path = tmp_path / 'catalog.json'
    catalog = json.loads(path.read_text())
    catalog['entries'][0]['style'].update(hub_z=-95.8, flank_w=16, flank_depth=22)
    path.write_text(json.dumps(catalog))
    d = review('20寸5辐直辐，外圈15个盲窗，孔距5×112', {})
    r = d['recipe']
    assert r['web_thick_hub'] >= 32
    assert r['hub_z'] + r['width'] / 2 - r['web_thick_hub'] == pytest.approx(35)
    assert d['adjustments'][0]['web_before_mm'] == pytest.approx(3.5)
    assert d['parameters']['hub_z']['source'] == 'rule'
    assert (r['flank_w'], r['flank_depth']) == (4, 6)


def test_deeper_request_preserves_bevel_and_records_only_real_change():
    from wheelcam.text_wheel import review
    a = review('20×10.5 ET15，5辐直辐', {})
    b = review('20×10.5 ET15，5辐直辐，凹深一点', {'style': {'flank_depth': 0}})
    assert b['recipe']['hub_z'] < a['recipe']['hub_z']
    assert b['recipe']['flank_depth'] == 6
    assert b['parameters']['hub_z']['source'] == 'user'
    c = review('20寸5辐直辐，凹深一点', {'style': {'flank_depth': 0}})
    assert c['recipe']['web_thick_hub'] >= 32
    assert c['parameters']['hub_z']['source'] != 'user'


def test_run_cannot_pass_thin_center_even_if_mesh_checks_pass(tmp_path, monkeypatch):
    from wheelcam import text_wheel as tw
    original = tw.review
    def bad_review(*args):
        d = original(*args)
        d['recipe']['hub_z'] = -95.8
        d['recipe']['web_thick_hub'] = 3.5
        return d
    monkeypatch.setattr(tw, 'review', bad_review)
    result = tw.run('20寸5辐直辐', tmp_path / 'thin', ask=lambda _: {})
    assert not result['checks']['hub_web_thickness']['pass']
    assert not result['all_checks_pass']
