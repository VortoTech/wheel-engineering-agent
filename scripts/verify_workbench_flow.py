"""Real-model workbench acceptance; run on the target CAD host (no model mocks)."""
import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from wheelcam.workbench import create_app
from wheelcam.workbench_revision import digest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--revision', required=True)
    a = ap.parse_args()
    if a.out.exists() and any(a.out.iterdir()):
        ap.error('use an empty output directory')
    a.out.mkdir(parents=True, exist_ok=True)
    client = TestClient(create_app(a.out))
    record = {'revision': a.revision, 'platform': platform.platform(), 'machine': platform.machine(),
              'models': httpx.get('http://127.0.0.1:8000/v1/models', timeout=10).json(),
              'scope': 'existing M59 development case; real model and CAD on target host; not holdout generalization',
              'input_sha256': {p: hashlib.sha256((a.case / p).read_bytes()).hexdigest() for p in ('front.jpg','spec.json')},
              'scenarios': []}

    def state(name):
        r = client.get('/api/runs/' + name); r.raise_for_status(); return r.json()

    def post(path, **kwargs):
        r = client.post(path, **kwargs)
        assert r.status_code == 200, f'{path}: {r.status_code} {r.text[:400]}'
        return r.json()

    def delivery(name, scenario):
        s = state(name)
        expected = s['current_preview']['recipe_sha256']
        r = post('/api/runs/' + name + '/deliver', json={'expected_recipe_sha256': expected})
        scenario['delivery'] = r
        path = a.out / r['name']
        manifest = json.loads((path / 'delivery_manifest.json').read_text())
        scenario['manifest'] = manifest
        assert manifest['complete'], r['chain']['steps']
        assert manifest['source_recipe_sha256'] == expected
        assert digest(json.loads((path / 'reconstruct/recipe.json').read_text())) == expected
        for artifact, evidence in manifest['artifacts'].items():
            assert hashlib.sha256((path / artifact).read_bytes()).hexdigest() == evidence['sha256'], artifact
        for artifact in ('machining/machining.step', 'machining/stock.step', 'drawing.svg', 'package/process_plan.json'):
            assert client.get('/files/' + r['name'] + '/' + artifact).status_code == 200
        assert state(r['name'])['current_preview']['downstream_stale'] is False
        scenario['all_artifact_hashes_verified'] = True

    def image_flow(scenario):
        spec = json.loads((a.case / 'spec.json').read_text())['spec']
        target_et = spec.pop('et_mm')
        created = post('/api/runs/image', files={'front': ('front.jpg', (a.case / 'front.jpg').read_bytes(), 'image/jpeg')},
                       data={'spec_json': json.dumps(spec), 'hole_form': '15X32X60'})
        scenario['image'] = created
        name = created['name']; initial = state(name)
        assert 'et_mm' not in initial['chain']['spec']
        assert 'et_mm' in initial['engineering_report']['engineering_understanding']['planning_decision']['missing_key_specifications']
        assert initial['engineering_report']['parameters']['bolt_d']['source'] == 'user'
        revision = post('/api/runs/' + name + '/spec', json={
            'expected_recipe_sha256':initial['current_preview']['recipe_sha256'],
            'confirmed':True, 'spec':{'et_mm':target_et}})
        scenario['confirmation'] = revision
        name = revision['name']; confirmed = state(name)
        assert confirmed['chain']['spec']['et_mm'] == target_et
        assert confirmed['engineering_report']['parameters']['et_mm']['source'] == 'user'
        old_hash = confirmed['current_preview']['recipe_sha256']
        scenario['style_turns'] = []
        for width in (4, 2):
            turn = post('/api/runs/' + name + '/chat', json={'message': f'将窗口侧斜面宽度 flank_w 设置为 {width} mm，其他参数保持不变。'})
            scenario['style_turns'].append(turn)
            assert turn['passed'], turn
            recipe = json.loads((a.out / name / 'chat' / turn['dir'] / 'recipe.json').read_text())
            assert recipe['flank_w'] == width, recipe['flank_w']
            assert recipe['pcd'] == spec['pcd_mm']
            assert state(name)['current_preview']['downstream_stale'] is True
        before_refusal = state(name)['current_preview']['recipe_sha256']
        refused = post('/api/runs/' + name + '/chat', json={'message':'把 PCD 改成 114.3 mm，不改其他参数。'})
        scenario['engineering_chat_refusal'] = refused
        assert refused['refused'] and refused['dir'] is None, refused
        assert state(name)['current_preview']['recipe_sha256'] == before_refusal
        stale = client.post('/api/runs/' + name + '/deliver', json={'expected_recipe_sha256':old_hash})
        scenario['stale_request_status'] = stale.status_code
        assert stale.status_code == 409
        delivery(name, scenario)

    def text_flow(scenario):
        created = post('/api/runs/text', json={'message':'20×10.5 ET15，5×112，中心孔66.6，孔型15X32X60。做5辐直辐，外圈加15个盲窗。'})
        scenario['text'] = created
        s = state(created['name'])
        expected = {'diameter_in':20,'width_in':10.5,'et_mm':15,'pcd_mm':112,'bolts':5,'center_bore_mm':66.6}
        assert s['chain']['spec'] == expected
        assert s['chain']['preview_only'] is True
        assert s['chain']['hole_form'] == '15X32X60'
        for key in (*expected, 'hole_form'):
            assert s['text_report']['parameters'][key]['source'] == 'user'
        delivery(created['name'], scenario)

    def missing_text(scenario):
        created = post('/api/runs/text', json={'message':'做一个21寸的6辐Y形轮毂'})
        scenario['text'] = created
        s = state(created['name'])
        assert s['chain']['spec']['diameter_in'] == 21
        assert s['text_report']['parameters']['spokes']['value'] == 6
        assert s['text_report']['parameters']['et_mm']['source'] == 'default'
        assert 'et_mm' in s['text_report']['unknown']
        assert s['text_report']['questions'] and s['text_report']['readiness'] == 'L0'
        assert not json.loads((a.out / created['name'] / 'delivery_manifest.json').read_text())['complete']

    for name, fn in [('image_confirm_edit_deliver',image_flow), ('text_deliver',text_flow), ('text_unknowns',missing_text)]:
        scenario = {'name': name}; start = time.monotonic()
        try:
            fn(scenario); scenario['passed'] = True
        except Exception as exc:
            scenario.update(passed=False, error=f'{type(exc).__name__}: {exc}')
        scenario['seconds'] = round(time.monotonic() - start, 2)
        record['scenarios'].append(scenario)
        record['all_passed'] = len(record['scenarios']) == 3 and all(s['passed'] for s in record['scenarios'])
        (a.out / 'acceptance.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in scenario.items() if k in ('name','passed','seconds','error')},ensure_ascii=False),flush=True)
    raise SystemExit(0 if record['all_passed'] else 1)


if __name__ == '__main__':
    main()
