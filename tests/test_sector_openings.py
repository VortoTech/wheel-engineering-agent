import copy
import json
from pathlib import Path

import numpy as np
import pytest

from wheelcam.sector_surface import (SurfaceControls, build_surface, perforated_triangulation,
                                     cross, hole_shoulder, opening_shape_metrics,
                                     loop_clearance, opening_rib_metrics)


@pytest.fixture(scope="module")
def evidence():
    return json.loads((Path(__file__).resolve().parents[1]/"experiments/sector-study/annotations.json").read_text())


def test_multi_loop_domain_has_exact_area_and_no_faces_across_holes():
    outer = np.array([[0, 0], [12, 0], [12, 10], [0, 10]], float)
    holes = [np.array([[2, 2], [4, 2], [4, 4], [2, 4]], float),
             np.array([[7, 3], [10, 3], [10, 7], [7, 7]], float)]
    loops, p, f = perforated_triangulation(outer, holes, spacing=1)
    assert len(loops) == 3
    area = abs(cross(p[f[:, 1]]-p[f[:, 0]], p[f[:, 2]]-p[f[:, 0]])).sum()/2
    assert area == pytest.approx(120-4-12)
    # Independent barycentric check: neither opening's center is covered.
    for q in [[3, 3], [8.5, 5]]:
        signs = np.stack([cross(p[f[:, (j+1) % 3]]-p[f[:, j]], np.array(q)-p[f[:, j]]) for j in range(3)])
        assert not np.any(np.all(signs >= -1e-10, axis=0) | np.all(signs <= 1e-10, axis=0))


@pytest.mark.parametrize("holes", [
    [[[9, 3], [11, 3], [11, 5], [9, 5]]],
    [[[1, 1], [5, 1], [5, 5], [1, 5]], [[2, 2], [3, 2], [3, 3], [2, 3]]],
    [[[1, 1], [5, 1], [5, 5], [1, 5]], [[4, 4], [6, 4], [6, 6], [4, 6]]],
    [[[.1, 2], [2, 2], [2, 4], [.1, 4]]],
    [[[2, 2], [3, 2], [4, 2]]],
])
def test_openings_reject_escape_nesting_overlap_and_vanishing_ribs(holes):
    with pytest.raises(ValueError):
        perforated_triangulation([[0, 0], [10, 0], [10, 10], [0, 10]], holes)


def test_through_and_recess_are_different_connected_topologies(evidence):
    through = build_surface(evidence, SurfaceControls())
    recess = build_surface(evidence, SurfaceControls(opening_mode="recess"))
    for result in (through, recess):
        assert result["integrity"]["connected_components"] == 1
        assert result["integrity"]["nonmanifold_edges"] == 0
        assert result["integrity"]["min_axial_thickness"] > 0
        assert result["openings"]["depth_measured"] is False
        assert result["gate"]["passed"] is False
    assert through["integrity"]["euler_characteristic"] == -2  # one closed genus-2 shell
    assert recess["integrity"]["euler_characteristic"] == 2
    assert through["openings"]["inner_wall_triangles"] > 0
    assert recess["openings"]["inner_wall_triangles"] == 0


def test_opening_edit_changes_hole_not_original_spoke_or_camera(evidence):
    changed = copy.deepcopy(evidence)
    changed["uncertain_pockets"][0][0][0] += .5
    before, after = [build_surface(e, SurfaceControls()) for e in (evidence, changed)]
    assert before["camera"] == after["camera"]
    assert before["openings"]["contours"] != after["openings"]["contours"]
    old, untouched = [build_surface(e, SurfaceControls(surface_scope="spoke")) for e in (evidence, changed)]
    assert old["positions"] == untouched["positions"]
    assert old["openings"]["count"] == 0
    assert old["integrity"]["euler_characteristic"] == 2
    assert old["diagnostic"]["max_px"] == pytest.approx(17.47147651215057)


def test_shoulder_is_local_bounded_and_keeps_outer_edge():
    outer = np.array([[0, 0], [20, 0], [20, 20], [0, 20]])
    hole = np.array([[4, 4], [10, 4], [10, 8], [4, 8]])
    points = np.array([[7, 4], [7, 3], [7, 2], [7, 0], [18, 18]])
    drop = hole_shoulder(points, outer, [hole], .004, 2)
    assert drop == pytest.approx([-.004, -.002, 0, 0, 0])
    assert np.array_equal(hole_shoulder(points, outer, [hole], 0, 2), np.zeros(5))


def test_fine_shoulder_changes_real_depth_without_changing_xy_or_annotations(evidence):
    before = copy.deepcopy(evidence)
    flat = build_surface(evidence, SurfaceControls(hole_shoulder_depth=0))
    rolled = build_surface(evidence, SurfaceControls())
    a, b = np.array(flat["positions"]), np.array(rolled["positions"])
    assert evidence == before
    assert rolled["camera"] == flat["camera"]
    assert rolled["openings"]["contours"] == flat["openings"]["contours"]
    assert rolled["triangles"] == flat["triangles"]
    np.testing.assert_array_equal(a[:, :2], b[:, :2])
    np.testing.assert_allclose(rolled["boundary_xyz"], flat["boundary_xyz"], atol=1e-12)
    assert np.min(b[:, 2]-a[:, 2]) < -.0039
    np.testing.assert_array_equal(a[len(a)//2:], b[len(b)//2:])  # back remains unchanged
    faces = np.array(rolled["triangles"])
    area = np.linalg.norm(np.cross(b[faces[:, 1]]-b[faces[:, 0]], b[faces[:, 2]]-b[faces[:, 0]]), axis=1)
    assert area.min() > 1e-12


def test_opening_metrics_are_rotation_independent_and_expose_stocky_traces(evidence):
    horizontal = opening_shape_metrics([[0, -1], [8, -1], [8, 1], [0, 1]])
    vertical = opening_shape_metrics([[1, 0], [1, 8], [-1, 8], [-1, 0]])
    assert horizontal["major_extent_px"] == pytest.approx(vertical["major_extent_px"])
    assert horizontal["minor_extent_px"] == pytest.approx(vertical["minor_extent_px"])
    assert horizontal["aspect_ratio"] == pytest.approx(4)
    result = build_surface(evidence, SurfaceControls())
    assert len(result["openings"]["shape_metrics"]) == 2
    assert all(shape["aspect_ratio"] > 3 for shape in result["openings"]["shape_metrics"])
    assert all(shape["measured_in_image"] for shape in result["openings"]["shape_metrics"])


def test_rib_metrics_measure_outer_and_neighbour_gaps_without_claiming_thickness():
    outer = np.array([[0, 0], [20, 0], [20, 20], [0, 20]], float)
    holes = [np.array([[2, 3], [6, 3], [6, 5], [2, 5]], float),
             np.array([[10, 3], [14, 3], [14, 5], [10, 5]], float)]
    metrics = opening_rib_metrics(outer, holes)
    assert loop_clearance(holes[0], holes[1]) == pytest.approx(4)
    assert metrics[0]["to_outer_px"] == pytest.approx(2)
    assert metrics[0]["to_other_opening_px"] == pytest.approx(4)
    assert metrics[0]["minimum_visible_rib_px"] == pytest.approx(2)
    assert metrics[0]["measured_in_image"] is True


def test_current_lower_opening_keeps_a_visible_outer_rib(evidence):
    result = build_surface(evidence, SurfaceControls())
    ribs = result["openings"]["rib_metrics"]
    assert ribs[0]["to_outer_px"] > 1.5
    assert ribs[1]["to_outer_px"] > 1.0
    assert min(rib["to_other_opening_px"] for rib in ribs) > 20


def test_legacy_preset_and_fine_sampling_preserve_holes(evidence):
    legacy = build_surface(evidence, SurfaceControls(root_sampling="legacy", hole_shoulder_depth=0))
    fine = build_surface(evidence, SurfaceControls())
    # Vertex count legitimately changes when the reviewed image-space opening
    # contour changes; retain the topology and refinement relationship instead
    # of freezing an obsolete trace's tessellation count.
    assert len(legacy["positions"]) > 4000
    assert len(fine["positions"]) > len(legacy["positions"])*2
    assert legacy["diagnostic"]["max_px"] == pytest.approx(17.24898, abs=1e-4)
    assert legacy["openings"]["contours"] == fine["openings"]["contours"]
    assert legacy["openings"]["shoulder"]["enabled"] is False
    assert fine["openings"]["shoulder"]["enabled"] is True
    assert fine["integrity"]["euler_characteristic"] == -2


@pytest.mark.parametrize("mode", ["through", "recess"])
def test_shoulder_extremes_keep_positive_thickness_and_connected_shell(evidence, mode):
    result = build_surface(evidence, SurfaceControls(opening_mode=mode, thickness=.015,
        hole_shoulder_depth=.008, hole_shoulder_width_px=4, fairing_px=3))
    assert result["integrity"]["min_axial_thickness"] > 0
    assert result["integrity"]["connected_components"] == 1
    assert result["integrity"]["nonmanifold_edges"] == 0
    assert result["openings"]["shoulder"]["enabled"] is (mode == "through")
