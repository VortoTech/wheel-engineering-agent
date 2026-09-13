import json
import math
import struct
import zipfile

import cadquery as cq
import pytest
from pydantic import ValidationError

from wheelcam.geometry import build_wheel, export_model, inspect_shape
from wheelcam.models import Preparation, MaterialSpec, WheelSpec, migrate_spec
from wheelcam.presets import preset_spec
from wheelcam.preparation import feature_manifest, volume
from wheelcam.template import TEMPLATE_VERSION, layout


@pytest.mark.parametrize('overrides', [{}, {'spoke_count': 6, 'paired_gap_mm': 28, 'paired_tip_width_mm': 8, 'spoke_phase_deg': 17},
                                       {'spoke_count': 10, 'hub_diameter_mm': 190, 'paired_gap_mm': 26, 'paired_tip_width_mm': 8}])
def test_paired_solid_has_two_blades_and_rounded_through_slot(overrides):
    spec, _ = preset_spec('photo-paired-8')
    spec = WheelSpec.model_validate({**spec.model_dump(), **overrides})
    wheel, info = build_wheel(spec)
    shape = wheel.val()
    assert all(inspect_shape(shape, spec)['checks'].values())
    assert info['junction_fillet_applied_mm'] == spec.junction_fillet_mm
    solid = shape.Solids()[0]
    lay = layout(spec)
    section = lay['sections'][2]
    r, z = section['r'], section['front'] - 4
    edge = (section['width'] / 2 + spec.paired_gap_mm / 2) / 2
    for index in range(spec.spoke_count):
        theta = math.radians(spec.spoke_phase_deg + index * 360 / spec.spoke_count)
        def point(x, y, height=z):
            return (x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta), height)
        assert not solid.isInside(point(r, 0))  # gap, not 16 disconnected fake rods
        assert solid.isInside(point(r, edge))
        assert solid.isInside(point(r, -edge))
        root = lay['paired_slot']['start_r_mm']
        assert solid.isInside(point(root - 1, 0, lay['hub_front_z'] - 8))
        assert not solid.isInside(point(root + 1, 0, lay['hub_front_z'] - 8))
    assert lay['derived']['spoke_blade_count'] == 2 * spec.spoke_count


@pytest.mark.parametrize('overrides', [{'pocket_depth_mm': 4}, {'sweep_deg': 10},
    {'spoke_thickness_mm': 30, 'hub_thickness_mm': 48, 'paired_tip_width_mm': 4},
    {'paired_gap_mm': 50, 'paired_tip_width_mm': 6}, {'spoke_fillet_mm': 4}, {'spoke_style': 'unknown'}])
def test_invalid_paired_layout_rejected(overrides):
    spec, _ = preset_spec('photo-paired-8')
    with pytest.raises(ValidationError):
        WheelSpec.model_validate({**spec.model_dump(), **overrides})


def gltf(path):
    data = path.read_bytes()
    json_length = struct.unpack_from('<I', data, 12)[0]
    return json.loads(data[20:20 + json_length])


def test_paired_export_and_decorations_do_not_enter_engineering(tmp_path):
    spec, sources = preset_spec('photo-paired-8')
    prep = Preparation(material=MaterialSpec(name='assumed', density_kg_m3=2700))
    snapshot = {'model_id': 'paired-export', 'draft_revision': 1, 'template_version': TEMPLATE_VERSION,
                'spec': spec.model_dump(), 'sources': sources, 'preparation': prep.model_dump()}
    report = export_model(spec, tmp_path, prep, snapshot)
    step = cq.importers.importStep(str(tmp_path / 'wheel.step')).val()
    assert len(step.Solids()) == 1
    assert report['volume_mm3'] == pytest.approx(volume(step), rel=1e-5)
    assert report['preparation']['weight']['finished_kg'] == pytest.approx(volume(step) * 2700 / 1e9, abs=0.0001)
    assert report['preparation']['caliper']['status'] == 'not_checked'
    engineering = gltf(tmp_path / 'wheel.glb')
    presentation = gltf(tmp_path / 'presentation.glb')
    assert len(engineering['materials']) >= 2
    names = [node.get('name', '') for node in presentation['nodes']]
    assert sum(name.startswith('display-only-rim-bolt-') for name in names) == 40
    assert not any('display-only' in node.get('name', '') for node in engineering['nodes'])
    assert report['presentation']['status'] == 'display_only'
    manifest = json.loads((tmp_path / 'features.json').read_text())
    assert sum(f['kind'] == 'paired_through_slot' for f in manifest['features']) == 8
    assert sum(f['kind'] == 'paired_loft_surface' for f in manifest['features']) == 8
    assert not any('display-only' in f['id'] for f in manifest['features'])
    with zipfile.ZipFile(tmp_path / 'handoff.zip') as archive:
        assert 'presentation.glb' in archive.namelist()


def test_v3_migration_keeps_single_style_and_engineering_values():
    old = WheelSpec().model_dump()
    for key in ['spoke_style', 'spoke_phase_deg', 'paired_gap_mm', 'paired_tip_width_mm', 'paired_split_start_mm']:
        old.pop(key)
    sources = {key: {'kind': 'measurement', 'note': 'keep'} for key in old}
    new, new_sources = migrate_spec(old, sources)
    assert new['spoke_style'] == 'single'
    assert all(new[key] == value and new_sources[key] == sources[key] for key, value in old.items())
    assert migrate_spec(new, new_sources) is None


def test_paired_feature_orientation_tracks_phase():
    spec, _ = preset_spec('photo-paired-8')
    spec = spec.model_copy(update={'spoke_phase_deg': 12})
    manifest = feature_manifest(spec, {}, 'hash')
    slots = [f for f in manifest['features'] if f['kind'] == 'paired_through_slot']
    assert [f['parameters']['rotation_deg'] for f in slots] == [12 + i * 45 for i in range(8)]


def test_photo_refinement_adds_real_lip_and_preserves_legacy_defaults():
    original, _ = preset_spec('photo-paired-8')
    refined, _ = preset_spec('photo-paired-refined')
    shape, _ = build_wheel(refined)
    solid = shape.val().Solids()[0]
    assert all(inspect_shape(shape.val(), refined)['checks'].values())
    r, t = refined.rim_diameter_in*25.4/2-30, math.pi/8
    assert solid.isInside((r*math.cos(t), r*math.sin(t), 96))
    assert not solid.isInside((r*math.cos(t), r*math.sin(t), 104))
    assert layout(original)['front_lip'] is None
    assert layout(refined)['sections'][1]['width'] > layout(original)['sections'][1]['width']+15
    old = original.model_dump()
    for key in ['paired_shoulder_mm', 'paired_mid_mm', 'paired_tip_inset_mm', 'lip_extension_mm', 'lip_drop_mm']:
        old.pop(key)
    migrated, _ = migrate_spec(old, {})
    assert all(migrated[k] == v for k, v in old.items())
    assert migrated['lip_extension_mm'] == 0
