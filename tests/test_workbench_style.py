"""Manual styling uses the same geometry path without requiring an LLM."""
import json
from dataclasses import asdict
from wheelcam.forged_blank import recipe_from_dict

from fastapi.testclient import TestClient
from wheelcam.workbench import create_app
from wheelcam.workbench_revision import digest
from test_workbench import make_run
from test_mesh_build import outline_recipe


def test_manual_pockets_rebuild_and_keep_engineering_constraints(tmp_path):
    recipe = outline_recipe()
    recipe['lip_pockets'] = 0
    original = make_run(tmp_path, recipe)
    c = TestClient(create_app(tmp_path))
    response = c.post('/api/runs/m1/style', json={
        'expected_recipe_sha256': digest(recipe), 'lip_pockets': 15})
    assert response.status_code == 200, response.text
    name = response.json()['name']
    state = c.get(f'/api/runs/{name}').json()
    child = json.loads((tmp_path / name / 'reconstruct/recipe.json').read_text())
    assert child['lip_pockets'] == 15
    assert child['lip_pocket_r'] == [child['ring_r'] + 2, child['lip_face_r_in'] - 3]
    for key in ('lip_r', 'width', 'pcd', 'bolts', 'center_bore_r', 'web_thick_hub'):
        assert child[key] == asdict(recipe_from_dict(recipe))[key]
    assert state['current_preview']['downstream_stale'] is True
    assert all(v['pass'] for v in state['current_preview']['checks'].values())
    assert state['engineering_report']['parameters']['lip_pockets']['source'] == 'user'
    assert state['engineering_report']['parameters']['lip_pocket_r']['source'] == 'rule'
    assert json.loads((original / 'machining/recipe.json').read_text()) == recipe
    assert c.get('/api/runs/m1').json()['style_parameters']['lip_pockets'] == 0
    from wheelcam.mesh_build import build
    _, before = build(recipe)
    after = json.loads((tmp_path / name / 'reconstruct/report.json').read_text())
    assert before['volume_mm3'] - after['volume_mm3'] > 1000  # real material was removed


def test_manual_pockets_reject_stale_or_non_style_inputs(tmp_path):
    recipe = outline_recipe()
    make_run(tmp_path, recipe)
    c = TestClient(create_app(tmp_path))
    payload = {'expected_recipe_sha256': digest(recipe), 'lip_pockets': 15}
    assert c.post('/api/runs/m1/style', json={**payload, 'expected_recipe_sha256': 'old'}).status_code == 409
    for invalid in (-1, 41, 1.5, True):
        assert c.post('/api/runs/m1/style', json={**payload, 'lip_pockets': invalid}).status_code == 422
    assert c.post('/api/runs/m1/style', json={**payload, 'pcd': 120}).status_code == 422
    assert c.get('/api/runs').json() == ['m1']


def test_model_configuration_is_explicit_without_exposing_endpoint(tmp_path, monkeypatch):
    for prefix in ('CHAT', 'AGENT'):
        for key in ('BASE_URL', 'MODEL'):
            monkeypatch.delenv(f'WHEELCAM_{prefix}_{key}', raising=False)
    c = TestClient(create_app(tmp_path))
    assert c.get('/api/capabilities').json() == {'chat_configured': False, 'manual_style': True}
    monkeypatch.setenv('WHEELCAM_CHAT_BASE_URL', 'http://private-host/v1')
    monkeypatch.setenv('WHEELCAM_CHAT_MODEL', 'local-model')
    assert c.get('/api/capabilities').json() == {'chat_configured': True, 'manual_style': True}


def test_failed_manual_build_keeps_parent_and_hides_failed_run(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    recipe = outline_recipe()
    make_run(tmp_path, recipe)
    c = TestClient(create_app(tmp_path))

    def failed(command, **kwargs):
        out = Path(command[command.index('--out') + 1])
        out.mkdir()
        (out / 'chain.json').write_text(json.dumps({'steps': [
            {'step': 'reconstruct', 'ok': False, 'error': 'geometry check failed'}]}))
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(subprocess, 'run', failed)
    r = c.post('/api/runs/m1/style', json={'expected_recipe_sha256': digest(recipe), 'lip_pockets': 15})
    assert r.status_code == 422
    assert c.get('/api/runs').json() == ['m1']
    assert c.get('/api/runs/m1').json()['current_preview']['recipe_sha256'] == digest(recipe)
