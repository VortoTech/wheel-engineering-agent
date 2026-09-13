import json
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from wheelcam.app import create_app
from wheelcam.models import WheelSpec, migrate_spec
from wheelcam.photo_pose import project, rotation, front_z, unproject_front, fit_pose
from wheelcam.presets import preset_spec
from wheelcam.root_fitting import correct_root, landmarks
from wheelcam.template import layout, slot_half_width
from test_vision import reference


def spec():
    base = preset_spec('photo-paired-refined')[0]
    return WheelSpec(**{**base.model_dump(),'paired_gap_mm':37.26,'paired_tip_width_mm':6.09,
        'paired_shoulder_mm':29.06,'paired_mid_mm':17.99,'paired_gap_flare_mm':7.87,
        'paired_split_start_mm':0,'paired_root_round_mm':12,'spoke_phase_deg':5.5})


def pose(s):
    return dict(cx=350.,cy=310.,scale_px=265.,distance_radii=12.,rotation=rotation(.14,-.10,.035).tolist(),
                radius_mm=296.9,reference_z_mm=s.rim_width_in*25.4/2,angles_deg=[8,-6,2])


def test_camera_rays_roundtrip_on_assumed_dished_face():
    s=spec();p=pose(s)
    xy=np.array([[80,0],[120,25],[-120,25],[0,-170],[200,-20]])
    xyz=np.column_stack([xy,front_z(s,np.linalg.norm(xy,axis=1))])
    recovered=unproject_front(project(xyz,p),p,s)
    assert np.max(np.abs(recovered-xyz)) < .002


def synthetic_analysis(s,p):
    angles=np.linspace(0,2*np.pi,120,endpoint=False)
    ring=project(np.column_stack([296.9*np.cos(angles),296.9*np.sin(angles),np.full(len(angles),p['reference_z_mm'])]),p)
    lo,hi=ring.min(axis=0),ring.max(axis=0)
    a={'ellipse':dict(cx=float((lo[0]+hi[0])/2),cy=float((lo[1]+hi[1])/2),rx=float((hi[0]-lo[0])/2),ry=float((hi[1]-lo[1])/2)),
       'outer_points':ring.tolist(),'spokes':{'groups':8,'image_phase_deg':39.5},'traces':[],'image_size':[720,640]}
    for group in range(8):
        theta=math.radians(39.5+group*45)
        for sign in (-1,1):
            rows=[]
            for r in np.linspace(.86,.36,101):
                us=np.array([sign*20-3,sign*20+3]);radial=r*296.9
                xyz=np.column_stack([radial*np.cos(theta)-us*np.sin(theta),-radial*np.sin(theta)-us*np.cos(theta),np.full(2,front_z(s,radial))])
                rows.append({'accepted':True,'radius_ratio':float(r),'points':project(xyz,p).tolist()})
            a['traces'].append({'samples':rows})
    return a


def test_camera_fit_improves_groups_not_used_in_optimization():
    s=spec();a=synthetic_analysis(s,pose(s));result=fit_pose(a,s)
    assert result['status']=='fitted'
    assert set(result['held_out_groups']).isdisjoint(result['train_groups'])
    assert result['after_held_out_px'] < result['before_held_out_px']*.25
    assert result['ring_median_px'] < 1


def test_root_image_points_reconstruct_actual_shape_parameters():
    s=spec();a=synthetic_analysis(s,pose(s));a['camera_fit']={'pose':pose(s)}
    for group in (0,3,5):
        parameters,points=correct_root(a,s,group,landmarks(s,a,pose(s),group))
        for k,v in parameters.items(): assert v == pytest.approx(getattr(s,k),abs=.01)
        assert np.max(np.abs(np.array(points)-landmarks(s,a,pose(s),group))) < .01
    with pytest.raises(ValueError):correct_root(a,s,5,[[0,0],[1,1],[2,2]])
    with pytest.raises(ValueError):correct_root(a,s,5,[[-1,0],[1,1],[2,2]])


def test_independent_root_corner_changes_real_opening():
    from wheelcam.geometry import build_wheel,inspect_shape
    s=WheelSpec(**{**spec().model_dump(),'junction_fillet_mm':0,'spoke_phase_deg':0})
    lay=layout(s);slot=lay['paired_slot']
    assert slot_half_width(slot,slot['start_r_mm']) == pytest.approx(slot['radius_mm']-12)
    wheel,_=build_wheel(s);solid=wheel.val().Solids()[0]
    assert all(inspect_shape(wheel.val(),s)['checks'].values())
    # The previous half-circle began 8 mm farther out. These are actual B-rep probes.
    a=slot['start_r_mm'];z=float(front_z(s,a+1)-4)
    assert not solid.isInside((a+1,5,z))
    assert solid.isInside((a+1,19,z))


def test_root_edit_is_revision_bound_and_persists_as_new_analysis(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        p=c.post('/api/projects',json={'name':'edit root','preset':'photo-paired-refined'}).json()
        p=c.post(f"/api/projects/{p['id']}/images",files={'file':('ref.png',reference(),'image/png')}).json()
        path=f"/api/projects/{p['id']}"
        a=c.post(f"{path}/images/{p['primary_image_id']}/analyze",json={'expected_revision':p['revision']}).json()
        body={'expected_revision':p['revision'],'group':a['root_fit']['group'],'points':a['root_fit']['points']}
        other=c.post('/api/projects',json={'name':'other'}).json()
        assert c.post(f"/api/projects/{other['id']}/analyses/{a['id']}/root",json=body).status_code==404
        result=c.post(f"{path}/analyses/{a['id']}/root",json=body)
        assert result.status_code==200,result.text
        b=result.json();assert b['id']!=a['id'] and b['parent_analysis_id']==a['id']
        assert b['root_fit']['status']=='manual' and b['root_fit']['edited_points']==body['points']
        assert c.get(path).json()['revision']==p['revision']
        applied=c.post(f"{path}/analyses/{b['id']}/apply",json={'expected_revision':p['revision']}).json()
        assert applied['revision']==p['revision']+1
        assert c.post(f"{path}/analyses/{a['id']}/root",json=body).status_code==409
        # Correcting the freshly applied analysis is allowed, without another analysis pass.
        body['expected_revision']=applied['revision'];body['points']=b['root_fit']['points']
        assert c.post(f"{path}/analyses/{b['id']}/root",json=body).status_code==200
        job=c.post(f"{path}/builds",json={'expected_revision':applied['revision']}).json()
        snapshot=next(j for j in c.get(path).json()['jobs'] if j['id']==job['id'])['snapshot']
        assert snapshot['photo_analysis']['id']==b['id']
        assert snapshot['photo_analysis']['root_fit']['edited_points']==b['root_fit']['edited_points']


def test_v6_migration_preserves_existing_shape_and_provenance():
    old=preset_spec('photo-paired-refined')[0].model_dump();old.pop('paired_root_round_mm')
    sources={k:{'kind':'manual','note':'preserve'} for k in old}
    new,new_sources=migrate_spec(old,sources)
    assert new['paired_root_round_mm']==0
    assert all(new[k]==v and new_sources[k]==sources[k] for k,v in old.items())
