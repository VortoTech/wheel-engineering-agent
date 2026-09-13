import io
import math

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from wheelcam.app import create_app
from wheelcam.presets import preset_spec
from wheelcam.vision import detect


def reference(count=8, phase=7, center=(350, 320), radius=265):
    image = Image.new('RGB', (720, 640), '#bdbdbd')
    d = ImageDraw.Draw(image)
    cx, cy = center
    d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill='#eeeeee', outline='#181818', width=3)
    for i in range(count):
        t = math.radians(phase+i*360/count)
        for sign in (-1, 1):
            points = [(cx+x*math.cos(t)-y*math.sin(t), cy+x*math.sin(t)+y*math.cos(t))
                      for x, y in [(radius*.35, sign*radius*.08-5), (radius*.92, sign*radius*.08-5),
                                   (radius*.92, sign*radius*.08+5), (radius*.35, sign*radius*.08+5)]]
            d.polygon(points, fill='#181818')
    stream = io.BytesIO(); image.save(stream, 'PNG')
    return stream.getvalue()


@pytest.mark.parametrize('count,phase,center,radius', [(8, 7, (350,320),265), (6, 19, (390,300),220)])
def test_local_candidates_detect_geometry_without_fixed_photo_coordinates(tmp_path, count, phase, center, radius):
    photo = tmp_path/'ref.png'; photo.write_bytes(reference(count, phase, center, radius))
    spec, _ = preset_spec('photo-paired-refined')
    result = detect(photo, spec, 600)
    assert result['status'] == 'candidates'
    assert result['spokes']['groups'] == count
    assert result['ellipse']['cx'] == pytest.approx(center[0], abs=5)
    assert result['ellipse']['cy'] == pytest.approx(center[1], abs=5)
    assert result['ellipse']['rx'] == pytest.approx(radius, abs=5)
    phase_delta = (result['suggested_parameters']['spoke_phase_deg'] + phase) % (360/count)
    assert min(phase_delta, 360/count-phase_delta) < 3
    assert result['scale']['reference_outer_mm'] == 600
    assert 'offset_et_mm' not in result['suggested_parameters']


def test_blank_image_does_not_invent_candidates(tmp_path):
    photo = tmp_path/'blank.png'; Image.new('RGB', (720,640), 'white').save(photo)
    with pytest.raises(ValueError, match='边缘'):
        detect(photo, preset_spec('photo-paired-refined')[0])


def test_analysis_review_revision_and_snapshot(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as c:
        p = c.post('/api/projects', json={'name':'local detector','preset':'photo-paired-refined'}).json()
        p = c.post(f"/api/projects/{p['id']}/images", files={'file':('ref.png',reference(),'image/png')}).json()
        path = f"/api/projects/{p['id']}"
        before = p['spec'].copy()
        a = c.post(f"{path}/images/{p['primary_image_id']}/analyze", json={'expected_revision':p['revision']}).json()
        assert a['can_apply']
        fresh = c.get(path).json()
        assert fresh['revision'] == p['revision'] and fresh['spec'] == before
        assert fresh['photo_analysis']['id'] == a['id']
        other = c.post('/api/projects',json={'name':'other'}).json()
        assert c.post(f"/api/projects/{other['id']}/analyses/{a['id']}/apply",json={'expected_revision':other['revision']}).status_code == 404
        applied = c.post(f"{path}/analyses/{a['id']}/apply",json={'expected_revision':p['revision']})
        assert applied.status_code == 200
        after = applied.json()
        assert after['revision'] == p['revision']+1
        assert after['spec']['offset_et_mm'] == before['offset_et_mm']
        assert '非实测' in after['sources']['spoke_phase_deg']['note']
        assert c.post(f"{path}/analyses/{a['id']}/apply",json={'expected_revision':after['revision']}).status_code == 409
        job = c.post(f"{path}/builds",json={'expected_revision':after['revision']}).json()
        snapshot = c.get(path).json()['jobs'][0]['snapshot']
        assert snapshot['model_id'] == job['id']
        assert snapshot['photo_analysis']['image_sha256'] == a['image_sha256']
        assert snapshot['photo_analysis']['suggested_parameters'] == a['suggested_parameters']
    assert create_app(tmp_path, start_worker=False).state.store.project(p['id'])['photo_analysis']['id'] == a['id']
