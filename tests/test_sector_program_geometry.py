"""Kernel checks for the sector adapter, not wheel engineering certification.

These tests exercise the actual periodic spline helper and small XY cutting
plates. The full-wheel/STEP runner separately checks the production assembly.
Finite curve sampling is an observed approximation error, not a certified
Hausdorff bound or a minimum three-dimensional wall-thickness measurement.
"""
import math

import cadquery as cq
import numpy as np
import pytest

from wheelcam.geometry import _window_outline_wire
from wheelcam.models import WheelSpec
from wheelcam.sector_program import compile_sector, example_program, patch_feature, sample_feature
from wheelcam.windows import _closed, _point_segment


def sampled_native_deviation_mm(feature, outline, reference_samples=1024, native_samples=2000):
    """Bidirectional point-to-polyline estimate against a denser design curve."""
    reference = np.asarray(sample_feature(feature, count=reference_samples), dtype=float)
    edge = _window_outline_wire(outline).Edges()[0]
    native = np.asarray([(point.x, point.y) for point in edge.positions(
        np.linspace(0, 1, native_samples, endpoint=False))])
    return max(float(_point_segment(native, *_closed(reference)).max()),
               float(_point_segment(reference, *_closed(native)).max()))


@pytest.mark.parametrize("groups", [5, 6])
def test_actual_periodic_splines_stay_close_to_design_curves(groups):
    program = example_program("split_y", groups)
    candidates = [program, patch_feature(program, "root_slot_left", {"width_mm": 8})]
    for candidate in candidates:
        spec, _ = compile_sector(candidate, WheelSpec())
        for feature, outline in zip(candidate.features, spec.window_outlines_mm):
            observed = sampled_native_deviation_mm(feature, outline)
            assert observed <= .1, (groups, feature.id, observed)


def test_original_tight_cap_geometry_is_preserved_within_adapter_error_gate():
    # This is the original 4 mm cap, not a rounder replacement shape selected
    # merely to pass the approximation gate. The old 192-point adapter was
    # observed to deviate by 0.124 mm for this same intended opening.
    program = patch_feature(example_program("split_y", 5), "main_window", {
        "end_round_mm": 4,
        "tip_half_width_mm": 210 * math.sin(math.radians(.74 * 36)),
    })
    spec, manifest = compile_sector(program, WheelSpec())
    index = manifest["feature_map"]["main_window"]["outline_index"]
    feature = program.features[index]
    coarse_observed = sampled_native_deviation_mm(feature, sample_feature(feature, count=192))
    # Confirm that this fixture really exposes the old interpolation error.
    # Revisit this negative control if the native spline adapter is replaced.
    assert coarse_observed > .1, coarse_observed
    observed = sampled_native_deviation_mm(feature, spec.window_outlines_mm[index])
    assert observed <= .1, observed


def _cutters(spec, height=6):
    return [cq.Solid.extrudeLinear(_window_outline_wire(outline), [], cq.Vector(0, 0, height))
            for outline in spec.window_outlines_mm]


def _local_point(radius, transverse, angle_deg, z=3):
    angle = math.radians(angle_deg)
    return (radius * math.cos(angle) - transverse * math.sin(angle),
            radius * math.sin(angle) + transverse * math.cos(angle), z)


def test_root_slot_edit_changes_actual_cut_only_in_its_named_feature():
    original = example_program("split_y", 5)
    edited = patch_feature(original, "root_slot_left", {"width_mm": 8})
    before_spec, before_manifest = compile_sector(original, WheelSpec())
    after_spec, after_manifest = compile_sector(edited, WheelSpec())
    changed_index = before_manifest["feature_map"]["root_slot_left"]["outline_index"]
    assert changed_index == after_manifest["feature_map"]["root_slot_left"]["outline_index"]
    for index, (before, after) in enumerate(zip(before_spec.window_outlines_mm, after_spec.window_outlines_mm)):
        assert (before != after) == (index == changed_index)

    old_cutters, new_cutters = _cutters(before_spec), _cutters(after_spec)
    plate = cq.Solid.makeBox(500, 500, 6, cq.Vector(-250, -250, 0))
    old = plate.cut(*old_cutters).clean()
    new = plate.cut(*new_cutters).clean()
    assert old.isValid() and new.isValid()
    assert len(old.Solids()) == len(new.Solids()) == 1
    assert new.Volume() < old.Volume() - 1
    assert new.cut(old).Volume() < 1e-5
    removed = old.cut(new)
    # The change must be geometrically local, not merely another valid solid.
    assert removed.cut(new_cutters[changed_index]).Volume() < 1e-5

    left = original.features[changed_index]
    witness = _local_point(left.center_radius_mm, 3.5, left.center_angle_deg)
    assert old.isInside(witness)
    assert not new.isInside(witness)
    right = next(feature for feature in original.features if feature.id == "root_slot_right")
    unaffected_witness = _local_point(right.center_radius_mm, 3.5, right.center_angle_deg)
    assert old.isInside(unaffected_witness) and new.isInside(unaffected_witness)
    # Both slot cores remain open while the intervening root bridge remains.
    for slot in (left, right):
        core = _local_point(slot.center_radius_mm, 0, slot.center_angle_deg)
        assert not old.isInside(core) and not new.isInside(core)
    assert old.isInside((102, 0, 3)) and new.isInside((102, 0, 3))


def test_actual_cutters_do_not_use_hidden_hub_clipping_or_cut_rim_collar():
    spec, manifest = compile_sector(example_program("split_y", 5), WheelSpec())
    checks = manifest["sampled_outline_checks"]
    hub_keep = cq.Solid.makeCylinder(checks["hub_keep_with_margin_mm"], 6)
    inside_rim = cq.Solid.makeCylinder(checks["rim_keep_radius_mm"], 6)
    for cutter in _cutters(spec):
        assert cutter.isValid() and len(cutter.Solids()) == 1
        # These kernel booleans are stronger than checking polygon vertices:
        # there is no cut material inside the protected hub or beyond the rim.
        assert cutter.intersect(hub_keep).Volume() < 1e-5
        assert cutter.cut(inside_rim).Volume() < 1e-5
