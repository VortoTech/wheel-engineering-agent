"""Hand-fitted visual preset; photo proportions are not measured engineering dimensions."""
from .models import WheelSpec, default_sources


def preset_spec(name='single'):
    if name == 'single':
        spec = WheelSpec()
        return spec, default_sources()
    if name not in {'photo-paired-8', 'photo-paired-refined'}:
        raise ValueError('未知造型预设')
    spec = WheelSpec(spoke_style='paired', spoke_count=8, spoke_phase_deg=0,
        rim_diameter_in=22, rim_width_in=8.5, offset_et_mm=35,
        hub_diameter_mm=155, hub_thickness_mm=40, spoke_thickness_mm=16,
        spoke_width_hub_mm=43, spoke_width_rim_mm=30, spoke_crown_mm=0,
        spoke_fillet_mm=1, junction_fillet_mm=3, sweep_deg=0, face_curve=0.35,
        pocket_depth_mm=0, paired_gap_mm=34, paired_tip_width_mm=6,
        paired_split_start_mm=8, valve_diameter_mm=11.3, valve_angle_deg=270)
    if name == 'photo-paired-refined':
        spec = WheelSpec.model_validate({**spec.model_dump(), 'paired_shoulder_mm': 20,
            'paired_mid_mm': 12, 'paired_tip_inset_mm': 10, 'lip_extension_mm': 42,
            'lip_drop_mm': 22, 'paired_tip_width_mm': 6.5, 'spoke_phase_deg': 4,
            'valve_angle_deg': 274})
    sources = default_sources()
    for value in sources.values():
        value['note'] = '照片样例演示尺寸/隐藏结构假设；22×8.5J、ET35、5×114.3、CB66 均非照片测量'
    for key in ['spoke_style', 'spoke_count', 'spoke_phase_deg', 'paired_gap_mm', 'paired_tip_width_mm',
                'paired_split_start_mm', 'spoke_width_hub_mm', 'sweep_deg', 'paired_shoulder_mm',
                'paired_mid_mm', 'paired_tip_inset_mm', 'lip_extension_mm']:
        sources[key] = {'kind': 'manual', 'note': '人工对照用户正面照片：8 组双细辐；比例为人工拟合，非实测'}
    return spec, sources
