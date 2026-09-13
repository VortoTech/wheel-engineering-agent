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


def test_continuous_traces_fit_flared_shoulders_with_one_occluded_group(tmp_path):
    import numpy as np
    from wheelcam.contours import trace_blades, fit_sections
    from wheelcam.models import WheelSpec
    image = Image.new('RGB', (720, 640), '#bdbdbd')
    d = ImageDraw.Draw(image)
    cx, cy, radius, phase = 350, 320, 265, 7
    d.ellipse((cx-radius,cy-radius,cx+radius,cy+radius), fill='#dddddd')
    for group in range(8):
        theta = math.radians(phase+group*45)
        for sign in (-1, 1):
            points = []
            rs = np.linspace(.32, .91, 100)
            # Independently defined tapered silhouette, not the CAD implementation.
            for r in rs:
                half = np.interp(r, [.32,.4,.56,.78,.91], [.12,.13,.11,.082,.08])
                u = sign*half
                points.append((cx+radius*(r*math.cos(theta)-u*math.sin(theta)), cy+radius*(r*math.sin(theta)+u*math.cos(theta))))
            for r in rs[::-1]:
                u = sign*.062
                points.append((cx+radius*(r*math.cos(theta)-u*math.sin(theta)), cy+radius*(r*math.sin(theta)+u*math.cos(theta))))
            d.polygon(points, fill='#252525')
    # One group is hidden by a background-coloured occluder.
    d.rectangle((cx+100,cy-20,cx+205,cy+55), fill='#dddddd')
    photo = tmp_path/'flared.png'; image.save(photo)
    traces = trace_blades(photo, {'cx':cx,'cy':cy,'rx':radius,'ry':radius}, [720,640],
                           {'groups':8,'image_phase_deg':phase,'offset_ratio':.08})
    assert len(traces) == 16
    assert any(not s['accepted'] for t in traces for s in t['samples'])
    fit = fit_sections(traces, preset_spec('photo-paired-refined')[0], 8)
    assert fit['status'] == 'fitted'
    assert fit['after_rms_ratio'] < fit['before_rms_ratio']*.5
    assert fit['stations'][0]['width_ratio'] == pytest.approx(.26, abs=.015)
    assert fit['stations'][1]['width_ratio'] == pytest.approx(.22, abs=.015)
    assert all(s['support_groups'] >= 6 for s in fit['stations'])
    fitted = WheelSpec.model_validate({**preset_spec('photo-paired-refined')[0].model_dump(), **fit['parameters']})
    assert fitted.paired_shoulder_mm > 24  # previously impossible even when detected
    assert fitted.offset_et_mm == 35 and fitted.bolt_circle_mm == 114.3


def test_missing_contour_evidence_never_synthesizes_a_fitted_shape():
    from wheelcam.contours import fit_sections
    traces = [{'samples':[]} for _ in range(16)]
    result = fit_sections(traces, preset_spec('photo-paired-refined')[0], 8)
    assert result['status'] == 'insufficient'
    assert result['parameters'] == {}
