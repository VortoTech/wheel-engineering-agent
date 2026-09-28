import io
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from wheelcam.forged_blank import recipe_from_dict
from wheelcam.workbench import create_app
from wheelcam.workbench_revision import confirm_spec, digest, snapshot_report
from test_workbench import make_run
from test_mesh_build import outline_recipe


def test_confirm_dimensions_preserves_style_and_updates_mounting_face():
    recipe = outline_recipe(flank_w=4, lip_pockets=15)
    original = json.loads(json.dumps(recipe))
    new, spec = confirm_spec(recipe, {}, {'diameter_in': 21, 'width_in': 10.5, 'et_mm': 15,
                                          'pcd_mm': 112, 'bolts': 5, 'center_bore_mm': 66.6}, '15X32X60')
    assert recipe == original
    assert new['flank_w'] == 4 and new['lip_pockets'] == 15
    assert new['bolt_d'] == 15 and new['seat_d'] == 32
    assert new['hub_z'] + new['width'] / 2 - new['web_thick_hub'] == pytest.approx(15)
    assert new['outlines'][0][0][0] / original['outlines'][0][0][0] == pytest.approx(new['lip_r'] / recipe_from_dict(original).lip_r)
    with pytest.raises(ValueError):
        confirm_spec(recipe, spec, {'center_bore_mm': 110}, '15X32X60')
    with pytest.raises(ValueError):
        confirm_spec(recipe, spec, {'diameter_in': float('nan')}, None)


def test_confirmation_preserves_unknowns_and_provenance():
    original = {'parameters': {'width_in': {'value': 9, 'source': 'default'}},
                'questions': ['请提供 et_mm', '请提供 width_in'], 'unknown': {'et_mm': '缺失', 'material': '缺失'},
                'engineering_understanding': {'spec_evidence': {}}}
    report = snapshot_report(original, outline_recipe(), {'et_mm': 15}, {'et_mm': 15}, None)
    assert report['parameters']['width_in']['source'] == 'default'
    assert report['parameters']['et_mm']['source'] == 'user'
    assert report['unknown'] == {'material': '缺失'}
    assert report['questions'] == ['请提供 width_in']
    assert report['engineering_understanding']['planning_decision']['key_dimensions_confirmed'] is False
    assert report['readiness'] == 'L0'


def test_image_upload_normalizes_file_and_tags_user_specs(tmp_path, monkeypatch):
    captured = {}
    def fake(command, **kwargs):
        source = Path(command[2])
        captured.update(json.loads((source / 'spec.json').read_text()))
        with Image.open(source / 'front.jpg') as image:
            assert image.format == 'JPEG' and image.size == (32, 32)
        assert '--preview-only' in command
        out = Path(command[command.index('--out') + 1]); out.mkdir()
        (out / 'chain.json').write_text(json.dumps({'steps': [{'ok': True}]}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, 'run', fake)
    c = TestClient(create_app(tmp_path))
    assert c.post('/api/runs/image', files={'front': ('x.jpg', b'invalid')}).status_code == 422
    buf = io.BytesIO(); Image.new('RGB', (32,32)).save(buf, 'PNG')
    response = c.post('/api/runs/image', files={'front': ('../../x.png', buf.getvalue())},
                      data={'spec_json': '{"et_mm":15}', 'hole_form': '15X32X60'})
    assert response.status_code == 200, response.text
    assert captured['spec_evidence'] == {'et_mm': {'source': 'user'}}
    assert captured['style_agent'] is False


def test_delivery_uses_latest_accepted_recipe_and_keeps_parent(tmp_path, monkeypatch):
    recipe = outline_recipe(flank_w=4)
    run = make_run(tmp_path, recipe)
    (run / 'chat/01').mkdir(parents=True)
    recipe['flank_w'] = 2
    (run / 'chat/01/recipe.json').write_text(json.dumps(recipe))
    (run / 'chat/history.json').write_text(json.dumps([{'dir': '01'}]))
    def fake(command, **kwargs):
        snap = json.loads(Path(command[command.index('--snapshot') + 1]).read_text())
        assert snap['recipe']['flank_w'] == 2
        assert digest(snap['recipe']) == snap['recipe_sha256']
        assert snap['parent']['run'] == 'm1'
        assert '--preview-only' not in command
        out = Path(command[command.index('--out') + 1]); out.mkdir()
        (out / 'chain.json').write_text(json.dumps({'steps': [{'ok': True}, {'step': 'package', 'ok': False}]}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, 'run', fake)
    c = TestClient(create_app(tmp_path))
    before = (run / 'chain.json').read_bytes()
    assert c.post('/api/runs/m1/deliver', json={'expected_recipe_sha256':'stale'}).status_code == 409
    response = c.post('/api/runs/m1/deliver', json={'expected_recipe_sha256':digest(recipe)})
    assert response.status_code == 200, response.text
    assert response.json()['chain']['steps'][1]['ok'] is False
    assert (run / 'chain.json').read_bytes() == before


def test_spec_requires_confirmation_and_failed_build_keeps_parent(tmp_path, monkeypatch):
    recipe = outline_recipe()
    run = make_run(tmp_path, recipe)
    def fake(command, **kwargs):
        assert '--preview-only' in command
        out = Path(command[command.index('--out') + 1]); out.mkdir()
        (out / 'chain.json').write_text(json.dumps({'steps': [{'ok': False, 'error':'invalid solid'}]}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(subprocess, 'run', fake)
    c = TestClient(create_app(tmp_path))
    body = {'expected_recipe_sha256':digest(recipe), 'spec':{'et_mm': 15}}
    assert c.post('/api/runs/m1/spec', json=body).status_code == 422
    body['confirmed'] = True
    assert c.post('/api/runs/m1/spec', json=body).status_code == 422
    assert json.loads((run / 'machining/recipe.json').read_text()) == recipe
    assert c.get('/api/runs').json() == ['m1']


def test_snapshot_cli_rebuilds_and_replaces_old_artifact_evidence(tmp_path):
    import sys
    recipe = outline_recipe(flank_w=4)
    reference = tmp_path / 'reference'; reference.mkdir()
    Image.new('RGB', (32, 32)).save(reference / 'front.jpg')
    snapshot = tmp_path / 'snapshot.json'
    report = {'readiness': 'L0', 'questions': [], 'output_evidence': {'old.step': {}},
              'visual_check': {'status': 'measured'}}
    snapshot.write_text(json.dumps({'recipe':recipe, 'recipe_sha256':digest(recipe), 'spec':{},
                                    'report':report, 'reference':str(reference), 'parent':{'run':'original'}}))
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / 'new'
    result = subprocess.run([sys.executable, str(root / 'scripts/demo_chain.py'), '--snapshot', str(snapshot),
                             '--preview-only', '--out', str(out)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    chain = json.loads((out / 'chain.json').read_text())
    assert chain['steps'][0]['ok'], chain
    assert chain['recipe_sha256'] == digest(recipe)
    assert (out / 'reference/front.jpg').read_bytes() == (reference / 'front.jpg').read_bytes()
    report = json.loads((out / 'reconstruct/engineering_report.json').read_text())
    assert 'old.step' not in report['output_evidence'] and 'visual_check' not in report
    assert set(report['output_evidence']) == {'recipe.json','wheel.glb','report.json'}
    manifest = json.loads((out / 'delivery_manifest.json').read_text())
    assert manifest['complete'] is False and manifest['source_recipe_sha256'] == digest(recipe)
    assert 'reconstruct/wheel.glb' in manifest['artifacts']
    payload = json.loads(snapshot.read_text()); payload['recipe']['flank_w'] = 2
    snapshot.write_text(json.dumps(payload))
    bad = tmp_path / 'tampered'
    subprocess.run([sys.executable, str(root / 'scripts/demo_chain.py'), '--snapshot', str(snapshot),
                    '--preview-only', '--out', str(bad)], capture_output=True, timeout=90)
    assert json.loads((bad / 'chain.json').read_text())['steps'][0]['ok'] is False
    assert not (bad / 'reconstruct/wheel.glb').exists()
