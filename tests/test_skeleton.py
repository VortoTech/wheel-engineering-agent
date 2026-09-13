import json
import math

import pytest

from wheelcam.geometry import export_model
from wheelcam.models import WheelSpec, default_sources, migrate_spec
from wheelcam.presets import preset_spec
from wheelcam.template import layout
from wheelcam.contours import fit_sections


def skeleton_spec(**overrides):
    original = preset_spec('photo-paired-refined')[0].model_dump()
    return WheelSpec(**{**original, 'paired_gap_mm':37.26,'paired_tip_width_mm':6.09,
        'paired_shoulder_mm':28.95,'paired_mid_mm':17.99,'paired_gap_flare_mm':7.87,
        'paired_split_start_mm':0,'paired_root_round_mm':12,'spoke_phase_deg':5.5,
        'paired_window_root_mm':20,'paired_window_blend_mm':55,**overrides})


def test_window_curve_limits_and_legacy_migration():
    for overrides in ({'paired_window_blend_mm':30}, {'paired_window_root_mm':36},
                      {'spoke_style':'single'}, {'paired_window_blend_mm':float('nan')}):
        with pytest.raises(ValueError): skeleton_spec(**overrides)
    s=skeleton_spec(paired_window_root_mm=0)
    old=s.model_dump();old.pop('paired_window_root_mm');old.pop('paired_window_blend_mm')
    sources=default_sources();new,ns=migrate_spec(old,sources)
    assert all(new[k]==v and ns[k]==sources[k] for k,v in old.items())
    assert new['paired_window_root_mm']==0
    assert layout(WheelSpec(**new))['sections']==layout(s)['sections']
    assert len(layout(s)['sections'])==5


def test_station_widths_do_not_confuse_front_width_with_depth():
    s=skeleton_spec();lay=layout(s)
    assert len(lay['sections'])>5
    stations=lay['skeleton_stations']
    assert all(x['back_blade_width_mm']>=2.5 for x in stations)
    assert stations[-1]['blade_width_mm']==pytest.approx(s.paired_tip_width_mm,abs=.01)
    assert stations[-1]['depth_mm']==pytest.approx(s.spoke_thickness_mm*.7,abs=.01)
    # Old width inversion must not silently treat a dense root section as the quarter station.
    assert fit_sections([],s,s.spoke_count)['status']=='manual_window_active'


def test_web_is_real_material_and_windows_stay_open(tmp_path):
    import cadquery as cq
    from wheelcam.photo_pose import front_z
    s=skeleton_spec();report=export_model(s,tmp_path)
    assert all(report['checks'].values()) and report['solid_count']==1
    solid=cq.importers.importStep(str(tmp_path/'wheel.step')).val().Solids()[0]
    alpha=math.pi/s.spoke_count
    bottom=s.hub_diameter_mm/2+s.paired_window_root_mm
    for group in range(s.spoke_count):
        angle=math.radians(s.spoke_phase_deg)+2*alpha*group+alpha
        # Before the window is a web; farther out remains an open window, on every group.
        for r,filled in ((bottom-8,True),(bottom+8,False),(155,False)):
            z=float(front_z(s,r*math.cos(alpha))-3)
            assert solid.isInside((r*math.cos(angle),r*math.sin(angle),z)) is filled
        narrow=angle-alpha;r=125;z=float(front_z(s,r)-3)
        assert not solid.isInside((r*math.cos(narrow),r*math.sin(narrow),z))
    assert report['hub_fillet_applied_mm']==0
    assert report['skeleton']['window']['bottom_radius_mm']==bottom
    manifest=json.loads((tmp_path/'features.json').read_text())
    windows=[x for x in manifest['features'] if x['kind']=='interspoke_region']
    assert len(windows)==8 and all(x['parameters']['root_curve'] for x in windows)
