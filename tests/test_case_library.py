import io
import json
import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from wheelcam.app import create_app
from wheelcam.case_library import STYLE_KEYS
from wheelcam.contour_review import glb_triangles, measure, silhouette


def triangle_glb():
    # Vertices in glTF Y-up. Reader must restore CAD Z-up, including hierarchy translation.
    vertices = np.array([[0,0,0],[10,0,0],[0,0,-10]],dtype='<f4').tobytes()
    doc = {'asset':{'version':'2.0'},'buffers':[{'byteLength':len(vertices)}],
           'bufferViews':[{'buffer':0,'byteLength':len(vertices)}],
           'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3'}],
           'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}],
           'nodes':[{'children':[1],'translation':[2,0,-3]},{'mesh':0}],
           'scenes':[{'nodes':[0]}],'scene':0}
    metadata=json.dumps(doc).encode();metadata+=b' '*((-len(metadata))%4)
    return struct.pack('<4sII',b'glTF',2,28+len(metadata)+len(vertices))+struct.pack('<II',len(metadata),0x4E4F534A)+metadata+struct.pack('<II',len(vertices),0x004E4942)+vertices


@pytest.fixture
def setup(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        p=c.post('/api/projects',json={'name':'案例来源'}).json()
        image=io.BytesIO();Image.new('RGB',(80,80),'white').save(image,'PNG')
        p=c.post(f"/api/projects/{p['id']}/images",files={'file':('reference.png',image.getvalue(),'image/png')}).json()
        store=c.app.state.store
        job=store.enqueue(p['id'],p['revision'])
        path=tmp_path/'models'/job;path.mkdir();(path/'wheel.glb').write_bytes(triangle_glb())
        pose={'cx':30,'cy':50,'scale_px':20,'radius_mm':10,'reference_z_mm':0,'distance_radii':1e6,'rotation':np.eye(3).tolist()}
        with store.connection() as db:
            snapshot=json.loads(db.execute('SELECT snapshot FROM jobs WHERE id=?',(job,)).fetchone()[0])
            snapshot['photo_analysis']={'id':'saved-pose','image_id':p['primary_image_id'],'image_sha256':p['images'][0]['sha256'],'image_size':[80,80],'camera_fit':{'pose':pose}}
            db.execute("UPDATE jobs SET status='succeeded',report=?,snapshot=? WHERE id=?",(json.dumps({'limitations':['not measured']}),json.dumps(snapshot),job))
        yield c,p,job,store


def test_case_immutable_snapshot_retrieval_and_safe_apply(setup):
    c,p,job,store=setup;url=f"/api/projects/{p['id']}"
    created=c.post(url+'/cases',json={'job_id':job,'name':'可复用造型'})
    assert created.status_code==201
    case=created.json();assert case['engineering_verified'] is False
    assert c.post(url+'/cases',json={'job_id':job,'name':'覆盖'}).status_code==409
    target=c.post('/api/projects',json={'name':'目标','preset':'photo-paired-8'}).json();turl=f"/api/projects/{target['id']}"
    candidates=c.get(turl+'/case-candidates').json()
    match=next(x for x in candidates['candidates'] if x['id']==case['id'])
    assert match['changes']['spoke_style']=='single'
    applied=c.post(turl+f"/cases/{case['id']}/apply",json={'expected_revision':target['revision']})
    assert applied.status_code==200,applied.text
    fresh=applied.json()
    for key in set(target['spec'])-STYLE_KEYS:
        assert fresh['spec'][key]==target['spec'][key]
        assert fresh['sources'][key]==target['sources'][key]
    assert fresh['preparation']==target['preparation'] and fresh['applied_analysis_id'] is None
    assert fresh['spec']['spoke_width_hub_mm']==pytest.approx(38*(22*25.4+35)/(18*25.4+35),abs=.0001)
    assert c.post(turl+f"/cases/{case['id']}/apply",json={'expected_revision':1}).status_code==409
    new_job=store.enqueue(target['id'],fresh['revision'])
    assert store.project(target['id'])['jobs'][0]['snapshot']['case_selection']['case_id']==case['id']
    assert new_job!=job
    with TestClient(create_app(store.root,start_worker=False)) as restarted:
        assert next(x for x in restarted.get('/api/cases').json() if x['id']==case['id'])==case


def test_case_ownership_holdout_and_atomic_constraint_failure(setup):
    c,p,job,store=setup;url=f"/api/projects/{p['id']}"
    other=c.post('/api/projects',json={'name':'other'}).json()
    assert c.post(f"/api/projects/{other['id']}/cases",json={'job_id':job,'name':'bad'}).status_code==404
    case=c.post(url+'/cases',json={'job_id':job,'name':'保留','role':'evaluation'}).json()
    assert case['id'] not in [x['id'] for x in c.get(url+'/case-candidates').json()['candidates']]
    assert c.post(url+f"/cases/{case['id']}/apply",json={'expected_revision':p['revision']}).status_code==404
    # Force a valid source width whose scaled transfer exceeds the target schema limit.
    with store.connection() as db:
        record=json.loads(db.execute('SELECT record FROM visual_cases WHERE id=?',(case['id'],)).fetchone()[0])
        record.update(role='reference');record['spec']['spoke_width_hub_mm']=60
        db.execute('UPDATE visual_cases SET record=? WHERE id=?',(json.dumps(record),case['id']))
    target=c.post('/api/projects',json={'name':'small-to-large','preset':'photo-paired-8'}).json()
    target_url=f"/api/projects/{target['id']}"
    response=c.post(target_url+f"/cases/{case['id']}/apply",json={'expected_revision':target['revision']})
    assert response.status_code==422
    assert store.project(target['id'])==target


def test_actual_glb_node_transform_and_projected_boundary():
    triangles=glb_triangles(triangle_glb())
    assert triangles.shape==(1,3,3)
    assert triangles[0]==pytest.approx(np.array([[2,3,0],[12,3,0],[2,13,0]]),abs=1e-10)
    pose={'cx':30,'cy':50,'scale_px':20,'radius_mm':10,'reference_z_mm':0,'distance_radii':1e6,'rotation':np.eye(3).tolist()}
    edge=silhouette(triangle_glb(),json.dumps(pose),(80,80))
    assert edge[44,34] and edge[24,34] and edge[44,54]
    good=measure(edge,[[34,26],[34,33],[34,42]])
    bad=measure(edge,[[24,26],[24,33],[24,42]])
    assert good['mean_px']==0 and good['within_3px']==1
    assert bad['mean_px']==10 and bad['within_3px']==0


def test_review_provenance_persistence_and_request_boundaries(setup):
    c,p,job,store=setup;url=f"/api/projects/{p['id']}/builds/{job}/contour-reviews"
    body={'expected_revision':p['revision'],'label':'左侧轮廓','points':[[34,26],[34,33],[34,42]]}
    r=c.post(url,json=body);assert r.status_code==201,r.text
    review=r.json();assert review['metrics']['mean_px']==0
    assert review['analysis_id']=='saved-pose' and review['scope']=='annotated_boundary_only'
    assert review['model_sha256'] and review['image_sha256']
    assert c.get(f"/api/projects/{p['id']}/contour-reviews/{review['id']}").json()==review
    assert c.post(url,json={**body,'expected_revision':1}).status_code==409
    assert c.post(url,json={**body,'points':[[0,0],[80,1],[2,2]]}).status_code==422
    assert c.post(url,json={**body,'points':[[1,1]]*3}).status_code==422
    other=c.post('/api/projects',json={'name':'other'}).json()
    assert c.post(f"/api/projects/{other['id']}/builds/{job}/contour-reviews",json={**body,'expected_revision':1}).status_code==404
    assert c.get(f"/api/projects/{other['id']}/contour-reviews/{review['id']}").status_code==404
    with store.connection() as db:
        db.execute('UPDATE projects SET primary_image_id=NULL WHERE id=?',(p['id'],))
    assert c.post(url,json=body).status_code==409
