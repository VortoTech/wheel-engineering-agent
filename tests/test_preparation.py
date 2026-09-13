import hashlib
import json
import math
import zipfile

import cadquery as cq
import pytest
from pydantic import ValidationError

from wheelcam.geometry import build_wheel, export_model
from wheelcam.models import CaliperSpec, MaterialSpec, Preparation, StockSpec, WheelSpec, default_sources, migrate_spec
from wheelcam.preparation import check_preparation, feature_manifest, stock_shape, valve_geometry, volume
from wheelcam.template import TEMPLATE_VERSION, layout


def cylinder(radius=40, height=20):
    return cq.Solid.makeCylinder(radius, height, cq.Vector(0, 0, -height / 2))


def test_missing_inputs_never_pass():
    result = check_preparation(cylinder(), WheelSpec(), Preparation())
    assert {item['status'] for item in result.values()} == {'not_checked'}


def test_stock_containment_distance_removal_and_density_units():
    stock = StockSpec(outer_diameter_mm=100, height_mm=40, cavity_diameter_mm=0, front_web_mm=20)
    material = MaterialSpec(name='test density', density_kg_m3=3000)
    result = check_preparation(cylinder(), WheelSpec(), Preparation(stock=stock, material=material))
    assert result['stock']['status'] == 'contained'
    assert result['stock']['minimum_allowance_mm'] == pytest.approx(10)
    assert result['stock']['missing_volume_mm3'] == 0
    assert result['stock']['removal_percent'] == pytest.approx(68)
    assert result['weight']['finished_kg'] == pytest.approx(32000 * math.pi * 3000 / 1e9, abs=0.00005)
    assert result['weight']['stock_kg'] == pytest.approx(100000 * math.pi * 3000 / 1e9, abs=0.00005)


def test_cup_missing_material_despite_larger_outer_dimensions():
    # Its bounding cylinder is larger, but its inner cavity consumes the entire target.
    stock = StockSpec(outer_diameter_mm=100, height_mm=60, cavity_diameter_mm=85, front_web_mm=10)
    result = check_preparation(cylinder(), WheelSpec(), Preparation(stock=stock, material=MaterialSpec(name='test', density_kg_m3=2700)))
    assert result['stock']['status'] == 'missing_material'
    assert result['stock']['missing_volume_mm3'] == pytest.approx(32000 * math.pi)
    assert result['stock']['removal_percent'] is None
    assert result['stock']['removed_volume_mm3'] is None
    assert result['weight']['removed_kg'] is None


def test_insufficient_allowance_and_offset_stock():
    stock = StockSpec(outer_diameter_mm=100, height_mm=40, center_z_mm=9.5, cavity_diameter_mm=0, front_web_mm=20)
    result = check_preparation(cylinder(), WheelSpec(), Preparation(stock=stock))
    assert result['stock']['status'] == 'insufficient_allowance'
    assert result['stock']['minimum_allowance_mm'] == pytest.approx(0.5)
    shifted = stock.model_copy(update={'center_z_mm': 20})
    assert check_preparation(cylinder(), WheelSpec(), Preparation(stock=shifted))['stock']['status'] == 'missing_material'


def test_caliper_covers_full_rotation_and_mounting_face_offset():
    # Material exists only near 90 degrees, so a check at a single clock angle would miss it.
    wheel = cq.Workplane('XY').box(4, 4, 4).translate((0, 100, 35)).val()
    caliper = CaliperSpec(inner_radius_mm=90, outer_radius_mm=110, z_min_mm=-5, z_max_mm=5)
    result = check_preparation(wheel, WheelSpec(), Preparation(caliper=caliper))['caliper']
    assert result['status'] == 'interference'
    assert result['overlap_mm3'] == pytest.approx(64)
    moved = WheelSpec(offset_et_mm=0)
    result = check_preparation(wheel, moved, Preparation(caliper=caliper))['caliper']
    assert result['status'] == 'clear'
    assert result['minimum_clearance_mm'] == pytest.approx(28)


def test_caliper_gap_and_contact_are_not_interference_volume():
    wheel = cylinder(80, 4).translate((0, 0, 35))
    caliper = CaliperSpec(inner_radius_mm=90, outer_radius_mm=110, z_min_mm=-5, z_max_mm=5, required_clearance_mm=11)
    result = check_preparation(wheel, WheelSpec(), Preparation(caliper=caliper))['caliper']
    assert result['status'] == 'insufficient_clearance'
    assert result['minimum_clearance_mm'] == pytest.approx(10)
    assert result['overlap_mm3'] == 0
    contact = caliper.model_copy(update={'inner_radius_mm': 80, 'required_clearance_mm': 0})
    assert check_preparation(wheel, WheelSpec(), Preparation(caliper=contact))['caliper']['status'] == 'insufficient_clearance'


@pytest.mark.parametrize('data', [
    {'caliper': {'outer_radius_mm': 90}}, {'caliper': {'z_min_mm': 10}},
    {'caliper': {'required_clearance_mm': float('nan')}},
    {'stock': {'cavity_diameter_mm': 550}}, {'stock': {'front_web_mm': 500}},
    {'material': {'name': 'unknown', 'density_kg_m3': 0}},
    {'material': {'name': 'unknown', 'density_kg_m3': float('inf')}}, {'unexpected': 1},
])
def test_invalid_preparation_rejected(data):
    with pytest.raises(ValidationError):
        Preparation.model_validate(data)


@pytest.mark.parametrize('tilt', [-25, 25])
def test_valve_is_a_through_hole_in_actual_solid(tilt):
    spec = WheelSpec(valve_diameter_mm=11.3, valve_angle_deg=105, valve_tilt_deg=tilt)
    shape, _ = build_wheel(spec)
    solid = shape.val().Solids()[0]
    center, axis, length = valve_geometry(spec, layout(spec))
    for t in [-length / 2, -2, 0, 2, length / 2]:
        assert not solid.isInside(tuple(c + a * t for c, a in zip(center, axis)))
    # Material beside the bore survives.
    tangent = (-math.sin(math.radians(105)), math.cos(math.radians(105)), 0)
    assert solid.isInside(tuple(c + 7 * t for c, t in zip(center, tangent)))
    assert shape.val().isValid() and len(shape.val().Solids()) == 1


def test_new_spec_migration_preserves_combined_v2_parameters():
    v2 = WheelSpec(rim_diameter_in=22, rim_width_in=11, offset_et_mm=20,
                   hub_thickness_mm=60, spoke_thickness_mm=40, spoke_crown_mm=6).model_dump()
    v2 = {k: v for k, v in v2.items() if not k.startswith('valve_')}
    sources = {k: {'kind': 'measurement', 'note': 'original'} for k in v2}
    migrated, provenance = migrate_spec(v2, sources)
    assert all(migrated[k] == v for k, v in v2.items())
    assert migrated['valve_diameter_mm'] == 0
    assert provenance['spoke_thickness_mm'] == sources['spoke_thickness_mm']
    assert provenance['valve_diameter_mm']['kind'] == 'template'


def test_feature_manifest_counts_and_optional_features():
    spec = WheelSpec(valve_diameter_mm=11.3, pocket_depth_mm=0)
    result = feature_manifest(spec, {'model_id': 'test', 'draft_revision': 8}, 'hash')
    features = result['features']
    assert len({f['id'] for f in features}) == len(features)
    assert sum(f['kind'] == 'stepped_hole' for f in features) == spec.bolt_count
    assert sum(f['kind'] == 'interspoke_region' for f in features) == spec.spoke_count
    assert not any(f['kind'] == 'back_pocket' for f in features)
    assert any(f['id'] == 'valve-01' for f in features)
    assert all(f['status'] == 'draft' for f in features)
    ids = {f['id'] for f in features}
    assert all(set(op['feature_ids']) <= ids for op in result['operations'])


def test_complete_handoff_uses_one_snapshot_and_reimportable_geometry(tmp_path):
    spec = WheelSpec(valve_diameter_mm=11.3, valve_tilt_deg=10)
    preparation = Preparation(stock=StockSpec(), caliper=CaliperSpec(), material=MaterialSpec(name='assumed', density_kg_m3=2700))
    snapshot = {'model_id': 'integration-01', 'draft_revision': 4, 'spec': spec.model_dump(),
                'template_version': TEMPLATE_VERSION, 'sources': default_sources(), 'preparation': preparation.model_dump()}
    report = export_model(spec, tmp_path, preparation, snapshot)
    manifest = json.loads((tmp_path / 'features.json').read_text())
    assert report['model_id'] == manifest['model_id'] == 'integration-01'
    assert report['draft_revision'] == manifest['draft_revision'] == 4
    assert manifest['step_sha256'] == hashlib.sha256((tmp_path / 'wheel.step').read_bytes()).hexdigest()
    assert manifest['preparation'] == preparation.model_dump()
    assert json.loads((tmp_path / 'recipe.json').read_text()) == snapshot
    assert report['engineering_approved'] is False
    assert report['preparation']['stock']['status'] == 'contained'
    assert volume(cq.importers.importStep(str(tmp_path / 'stock.step')).val()) == pytest.approx(volume(stock_shape(preparation.stock)))
    assert (tmp_path / 'operations.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    with zipfile.ZipFile(tmp_path / 'handoff.zip') as archive:
        assert set(archive.namelist()) == {*report['artifacts'], 'report.json'}
        for name, evidence in report['artifacts'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == evidence['sha256']
        assert json.loads(archive.read('report.json')) == report


def test_nonfinite_kernel_distance_never_passes(monkeypatch):
    monkeypatch.setattr(cq.Shape, 'distance', lambda *args: float('nan'))
    with pytest.raises(ValueError, match='距离计算异常'):
        check_preparation(cylinder(), WheelSpec(), Preparation(stock=StockSpec(cavity_diameter_mm=0)))
