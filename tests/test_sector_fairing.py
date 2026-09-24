import json
from pathlib import Path

import numpy as np
import pytest

from wheelcam.sector_fairing import attachment_edges, fair_relief, thin_plate_operator
from wheelcam.sector_surface import SurfaceControls, build_surface, surface_triangulation


def grid():
    return surface_triangulation(np.array([[0, 0], [8, 0], [8, 8], [0, 8]], float), spacing=1)


def test_fem_operator_is_symmetric_constant_preserving_and_has_positive_mass():
    _, points, faces = grid()
    stiffness, mass = thin_plate_operator(points, faces)
    np.testing.assert_allclose(stiffness.toarray(), stiffness.T.toarray(), atol=1e-12)
    np.testing.assert_allclose(stiffness @ np.ones(len(points)), 0, atol=1e-12)
    assert mass.min() > 0
    assert mass.sum() == pytest.approx(64)


def test_fairing_reduces_energy_and_holds_boundary_exactly():
    boundary, points, faces = grid()
    target = np.zeros(len(points))
    target[len(boundary)+5] = .02
    result, report = fair_relief(points, faces, target, len(boundary), 1)
    np.testing.assert_array_equal(result[:len(boundary)], target[:len(boundary)])
    assert report["energy_after"] < report["energy_before"]*.5
    assert 0 < result.max() < target.max()
    unchanged, disabled = fair_relief(points, faces, target, len(boundary), 0)
    np.testing.assert_array_equal(unchanged, target)
    assert disabled["max_displacement_R"] == 0


def test_attachment_arc_wraps_correctly_and_rejects_detached_endpoints():
    boundary = np.array([[0, 0], [4, 0], [4, 4], [0, 4], [-.2, 2]])
    mask = attachment_edges(boundary, [[[0, 0], [0, 4]]])
    np.testing.assert_array_equal(mask, [False, False, False, True, True])
    with pytest.raises(ValueError, match="偏离边界"):
        attachment_edges(boundary, [[[20, 30], [22, 30]]])


@pytest.mark.parametrize("width", [0, 1, 3])
def test_fairing_preserves_all_boundary_heights_and_closed_positive_thickness(width):
    evidence = json.loads((Path(__file__).resolve().parents[1]/"experiments/sector-study/annotations.json").read_text())
    baseline = build_surface(evidence, SurfaceControls(fairing_px=0))
    result = build_surface(evidence, SurfaceControls(fairing_px=width))
    np.testing.assert_array_equal(result["boundary_xyz"], baseline["boundary_xyz"])
    assert result["fairing"]["energy_after"] <= result["fairing"]["energy_before"]+1e-12
    assert result["integrity"]["nonmanifold_edges"] == 0
    assert result["integrity"]["min_axial_thickness"] > 0
    assert result["junctions"]["connected_to_hub_or_rim"] is False


def test_degenerate_faces_are_rejected_by_fairing():
    with pytest.raises(ValueError, match="退化三角形"):
        thin_plate_operator(np.array([[0, 0], [1, 0], [2, 0]]), np.array([[0, 1, 2]]))
