import math
import json

import pytest

from wheelcam.models import WheelSpec, default_sources, migrate_spec
from wheelcam.template import layout, slot_half_width
from test_skeleton import skeleton_spec


def test_taper_constraints_and_zero_mode_migration():
    for patch in ({'paired_blade_root_mm':5}, {'paired_window_root_mm':0}, {'spoke_crown_mm':1}):
        with pytest.raises(ValueError):skeleton_spec(**{'paired_blade_root_mm':11,**patch})
    s=skeleton_spec();old=s.model_dump();old.pop('paired_blade_root_mm')
    new,sources=migrate_spec(old,default_sources())
    assert new['paired_blade_root_mm']==0
    assert layout(WheelSpec(**new))==layout(s)
    assert all(new[k]==v for k,v in old.items())


def test_explicit_profile_ignores_obsolete_bulge_controls():
    s=skeleton_spec(paired_blade_root_mm=11)
    other=skeleton_spec(paired_blade_root_mm=11,paired_shoulder_mm=0,paired_mid_mm=0)
    assert layout(s)['explicit_profile']==layout(other)['explicit_profile']
    stations=layout(s)['skeleton_stations'][2:]
    assert all(a['blade_width_mm']>=b['blade_width_mm'] for a,b in zip(stations,stations[1:]))
    assert all(x['blade_width_mm']==x['back_blade_width_mm'] for x in stations)


def test_exported_silhouette_follows_monotone_width_without_loft_overshoot(tmp_path):
    import cadquery as cq
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.gp import gp_Lin,gp_Pnt,gp_Dir
    from wheelcam.geometry import export_model
    s=skeleton_spec(paired_blade_root_mm=11)
    report=export_model(s,tmp_path)
    assert all(report['checks'].values())
    shape=cq.importers.importStep(str(tmp_path/'wheel.step')).val()
    probe=IntCurvesFace_ShapeIntersector();probe.Load(shape.wrapped,1e-7)
    lay=layout(s);join=s.hub_diameter_mm/2+s.paired_window_blend_mm;end=lay['sections'][-1]['r']
    for group in (0,3,7):
        angle=math.radians(s.spoke_phase_deg+group*360/s.spoke_count)
        c,sn=math.cos(angle),math.sin(angle)
        def hits(r,u):
            probe.Perform(gp_Lin(gp_Pnt(r*c-u*sn,r*sn+u*c,-500),gp_Dir(0,0,1)),0,1000)
            return probe.NbPnt()>0
        for r in range(140,231,10):
            inner=slot_half_width(lay['paired_slot'],r)
            # The requested width is a linear taper, independently checked against serialized CAD.
            width=11+(s.paired_tip_width_mm-11)*(r-join)/(end-join)
            assert hits(r,inner+.02) and not hits(r,inner-.02)
            assert hits(r,inner+width-.02) and not hits(r,inner+width+.02)
    assert report['spoke_fillet_applied_mm']==0 and report['hub_fillet_applied_mm']==0
    assert report['skeleton']['explicit_profile']['wall_type']=='vertical'
    features=json.loads((tmp_path/'features.json').read_text())['features']
    assert len([f for f in features if f['kind']=='paired_profile_surface'])==8
