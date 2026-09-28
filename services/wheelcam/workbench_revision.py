"""Immutable workbench snapshots and explicit engineering confirmations.

Style chat remains locked. Only this user-confirmed path changes engineering inputs.
"""
import copy
import hashlib
import json
from dataclasses import asdict

from .wheel_skill_contract import WheelInputSpec


def digest(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def confirm_spec(recipe, previous_spec, changes, form):
    from .forged_blank import recipe_from_dict, hole_form
    from .wheel_skill import envelope_from_specs
    changes = WheelInputSpec.model_validate(changes).model_dump(exclude_none=True)
    spec = WheelInputSpec.model_validate({**previous_spec, **changes}).model_dump(exclude_none=True)
    # Keep the same supported envelope as text generation.
    bounds = {'diameter_in': (16, 24), 'width_in': (6, 15), 'pcd_mm': (80, 180),
              'bolts': (3, 12), 'center_bore_mm': (45, 120), 'et_mm': (-30, 75)}
    for key, value in spec.items():
        if not bounds[key][0] <= value <= bounds[key][1]:
            raise ValueError(f'{key} 超出当前支持范围 {bounds[key]}')
    updated = asdict(recipe_from_dict(copy.deepcopy(recipe)))
    envelope, _ = envelope_from_specs({k: spec[k] for k in changes})
    # Unrelated confirmations must not silently replace an existing seat.
    envelope.pop('seat_d', None)
    old_lip = updated['lip_r']
    updated.update(envelope)
    scale = updated['lip_r'] / old_lip
    if scale != 1:
        for key in ('window_r_in', 'window_r_out', 'split_r', 'window_pocket_r', 'window_through_r', 'hub_crease_r'):
            updated[key] *= scale
        updated['outlines'] = [[[r * scale, a] for r, a in loop] for loop in updated['outlines']]
        for key in ('lip_pocket_r', 'outline_groove_r', 'spoke_pad_r'):
            updated[key] = [v * scale for v in updated[key]]
        updated['stem_slots'] = [[v * scale for v in slot] for slot in updated['stem_slots']]
        if updated.get('skeleton'):
            updated['skeleton']['nodes'] = {k: [v[0] * scale, v[1]] for k, v in updated['skeleton']['nodes'].items()}
    if 'et_mm' in spec:
        updated['web_thick_hub'] = round(updated['hub_z'] + updated['width'] / 2 - spec['et_mm'], 2)
    if form:
        parsed = hole_form(form)
        if not parsed:
            raise ValueError('孔型应为例如 15X32X60')
        updated.update(parsed)
    if updated['seat_d'] >= updated['pcd'] - 2 * updated['center_bore_r'] - 6:
        raise ValueError('PCD、中心孔和孔座之间不足 3 mm 壁厚，请核对尺寸和孔型')
    if updated['web_thick_hub'] <= 0:
        raise ValueError('当前凹度与宽度、ET 冲突，安装面厚度非正；请先调整凹度')
    return updated, spec


def snapshot_report(original, recipe, spec, changes, form, hole_confirmed=False):
    from .forged_blank import recipe_from_dict
    effective = asdict(recipe_from_dict(recipe))
    report = copy.deepcopy(original)
    parameters = report.setdefault('parameters', {})
    if 'spec' in report:
        report['spec'] = spec
    if 'hole_form' in report:
        report['hole_form'] = form
    for key in changes:
        parameters[key] = {'value': spec[key], 'source': 'user', 'note': '工作台工程尺寸明确确认'}
    if hole_confirmed:
        for key in ('bolt_d', 'seat_d', 'seat_cone_deg'):
            parameters[key] = {'value': effective[key], 'source': 'user', 'note': '工作台明确确认孔型'}
        parameters['hole_form'] = {'value': form, 'source': 'user', 'note': '工作台孔型明确确认'}
    fields = {'diameter_in': ('lip_r',), 'width_in': ('width',), 'pcd_mm': ('pcd', 'hub_r'),
              'center_bore_mm': ('center_bore_r',), 'bolts': ('bolts',), 'et_mm': ('web_thick_hub',)}
    for key in changes:
        for field in fields.get(key, ()):
            if field != key:
                parameters[field] = {'value': effective[field], 'source': 'rule', 'note': f'由确认的 {key} 更新'}
    if 'et_mm' in changes and 'et' in parameters:
        parameters['et'] = {'value': spec['et_mm'], 'source': 'user', 'note': '工作台明确确认'}
    if 'inputs' in report:
        report['inputs']['spec'] = spec
        report['inputs']['hole_form'] = form
    if 'diameter_in' in changes:
        parameters['outline_rescaling'] = {'source': 'rule', 'value': 'proportional',
                                          'note': '按确认直径同比缩放原窗口轮廓；未经新照片拟合'}
    confirmed = set(changes) | ({'hole_form'} if hole_confirmed else set())
    report['questions'] = [q for q in report.get('questions', []) if not any(k in q for k in confirmed)]
    unknown = report.get('unknown', {})
    if isinstance(unknown, dict):
        report['unknown'] = {k: v for k, v in unknown.items() if k not in confirmed}
    elif isinstance(unknown, list):
        report['unknown'] = [k for k in unknown if k not in confirmed]
    for field, record in report.get('recipe_parameters', {}).items():
        if field in recipe:
            value = len(recipe[field]) if field == 'outlines' else recipe[field]
            if record.get('value') != value:
                record.update(value=value, source='rule', note='本次确认或配方修改派生；见父版本记录')
            if field in parameters:
                record.update(parameters[field])
    understanding = report.get('engineering_understanding')
    if understanding:
        evidence = understanding.setdefault('spec_evidence', {})
        for key in changes:
            evidence[key] = {'source': 'user', 'note': '工作台工程尺寸明确确认'}
        from .forged_blank import recipe_from_dict
        from .wheel_skill_contract import understanding as understand
        report['engineering_understanding'] = understand(spec, parameters, report.get('unknown', {}),
                                                          report['questions'], recipe_from_dict(recipe), evidence)
    report.update(readiness='L0', manufacturing_status='not_released',
                  readiness_limits=['当前为网格草案；加工级 STEP 单独校验，不包含完整造型曲面'],
                  recipe_sha256=digest(recipe))
    return report
