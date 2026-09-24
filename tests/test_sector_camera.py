import copy
import json
from pathlib import Path

import numpy as np
import pytest

from wheelcam.sector_camera import fit_candidates, project, ring_residual, rotation
from wheelcam.sector_surface import lift, smooth_guide, guide_field


@pytest.fixture(scope="module")
def evidence():
    return json.loads((Path(__file__).resolve().parents[1]/"experiments/sector-study/annotations.json").read_text())


@pytest.mark.parametrize("distance", [3, 8, 1e6])
def test_perspective_conic_and_surface_ray_round_trip(distance):
    pose = {"cx": 987., "cy": 660., "scale": 402., "sag": .4,
            "rotation": rotation(.55, .12), "distance_radii": distance}
    angle = np.linspace(0, 2*np.pi, 60)
    rim = np.column_stack([np.cos(angle), np.sin(angle), np.zeros(len(angle))])
    assert np.max(abs(ring_residual(project(rim, pose), pose))) < 1e-8
    pixels = project(rim*.6, pose)
    np.testing.assert_allclose(project(lift(pixels, pose), pose), pixels, atol=1e-8)


def test_camera_candidates_ignore_all_spoke_observations_and_generalize_pixel_coordinates(evidence):
    original = fit_candidates(evidence)
    changed = copy.deepcopy(evidence)
    changed["validation"]["points"] = [[-999, 999]]
    changed["master"]["boundary"] = [[0, 0]]
    assert fit_candidates(changed) == original
    # Different image size and offset; no hardcoded bounds for this product photo.
    offset, scale = np.array([700, 350]), 2
    for key in ("rim_points", "hub_center"):
        changed[key] = (np.array(changed[key])*scale+offset).tolist()
    changed["structure"]["lug_centers"] = (np.array(changed["structure"]["lug_centers"])*scale+offset).tolist()
    other = fit_candidates(changed)
    assert other["distance_radii"] == original["distance_radii"]
    assert other["cx"] == pytest.approx(original["cx"]*scale+offset[0], abs=.02)
    assert other["cy"] == pytest.approx(original["cy"]*scale+offset[1], abs=.02)
    assert all(c["converged"] for c in original["candidates"])
    assert original["depth_measured"] is False


def test_spline_guides_retain_knots_and_taper_at_both_ends():
    guide = [[0, 0], [8, 2], [16, 5], [24, 6]]
    smooth = smooth_guide(guide)
    for point in guide:
        assert np.linalg.norm(smooth-point, axis=1).min() < 1e-8
    straight = [[[0, 0], [30, 0]]]
    points = np.array([[0, 0], [2, 0], [10, 0], [15, 0], [30, 0]])
    field = guide_field(points, straight, width=2, blend=10)
    np.testing.assert_allclose(field[[0, 2, 3, 4]], [0, 1, 1, 0], atol=1e-10)
    assert 0 < field[1] < 1
    # A guide union is bounded and smooth, not an additive double-height seam.
    union = guide_field(points, straight*2, width=2, blend=10)
    assert np.all((union >= field) & (union <= 1))
    np.testing.assert_array_equal(guide_field(points, [], 2, 10), np.zeros(len(points)))


def test_invalid_guides_and_camera_points_reject(evidence):
    with pytest.raises(ValueError):
        smooth_guide([[1, 1], [1, 1]])
    bad = copy.deepcopy(evidence)
    bad["rim_points"] = [[0, 0]]*18
    with pytest.raises(ValueError):
        fit_candidates(bad)
