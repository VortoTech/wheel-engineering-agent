"""Window fitting on a synthetic labelled photo rendered through a known camera."""
import math

import numpy as np
import pytest
from PIL import Image

from wheelcam import window_fit as wf
from wheelcam.models import WheelSpec
from wheelcam.photo_pose import front_z, project, rotation
from wheelcam.template import FLANGE_HEIGHT, INCH, layout
from wheelcam.windows import area, rotate

GROUPS = 5


def swept_window(n=24):
    """Drifts 25° from hub to rim: no radial line of the group stays clear of it."""
    rs = np.linspace(95, 205, n)
    centre = 20 + 25 * (rs - 95) / 110
    half = 7 + 6 * (rs - 95) / 110
    point = lambda r, a: (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
    return ([point(r, c + h) for r, c, h in zip(rs, centre, half)]
            + [point(r, c - h) for r, c, h in zip(rs[::-1], centre[::-1], half[::-1])])


def synthetic(spec, outline, pose):
    # Labels come from the target CAD controls, not the fitter's front_z helper.
    # This prevents a shared erroneous proxy from making a fit look correct.
    target = WheelSpec(**{**spec.model_dump(), "spoke_method": "window", "window_outlines_mm": [outline]})
    (r0, z0), (_, zm), (r1, z1) = layout(target)["window_blank"]["top_rz"]
    windows = []
    for g in range(GROUPS):
        p = np.array(rotate(outline, g * 360 / GROUPS))
        t = np.clip((np.hypot(*p.T)-r0)/(r1-r0), 0, 1)
        z = (1-t)**2*z0 + 2*(1-t)*t*zm + t*t*z1
        uv = project(np.column_stack([p, z]), pose)
        windows.append({"points": uv.tolist()})
    t = np.linspace(0, 2 * np.pi, 90, endpoint=False)
    outer = pose["radius_mm"]
    ring = project(np.column_stack([outer * np.cos(t), outer * np.sin(t), np.full(len(t), pose["reference_z_mm"])]), pose)
    return {"image": {"name": "synthetic", "sha256": "0" * 64, "width": 1400, "height": 1300},
            "rim": {**wf.fit_ellipse(ring), "rms_px": 0.0, "points": ring[::10].tolist()}, "hub": None,
            "windows": windows, "ignore": [], "meta": {"groups": GROUPS, "usable": True}}


@pytest.mark.parametrize("crown", [0, 4])
def test_fit_recovers_swept_window_and_camera(crown):
    spec = WheelSpec(spoke_count=GROUPS, rim_diameter_in=20, spoke_crown_mm=crown)
    outer = spec.rim_diameter_in * INCH / 2 + FLANGE_HEIGHT
    rim_z = spec.rim_width_in * INCH / 2 + layout(spec)["derived"]["flange_thickness_mm"]
    pose = {"cx": 700.0, "cy": 650.0, "scale_px": 560.0, "distance_radii": 5.0,
            "rotation": rotation(0.15, -0.05, 0.0).tolist(), "radius_mm": outer, "reference_z_mm": rim_z,
            "angles_deg": [math.degrees(0.15), math.degrees(-0.05), 0.0]}
    truth = swept_window()
    label = synthetic(spec, truth, pose)
    result = wf.fit(label, spec, *wf.label_masks(label))
    assert result["window_error"] is None
    assert result["base_surface"]["includes_crown"] is True
    assert "spoke_ridges" in result["base_surface"]["excluded"]
    assert len(result["window_outlines_mm"]) == 1
    assert result["held_out_iou_mean"] > 0.93
    assert result["within_image_consistency_iou"] == result["held_out_iou_mean"]
    assert result["metric_scope"] == "within_image_consistency_not_independent_validation"
    fitted = result["window_outlines_mm"][0]
    assert abs(area(fitted)) == pytest.approx(abs(area(truth)), rel=0.08)
    # The chosen camera tilts the right way (the ellipse alone cannot tell the sign).
    ax, ay, _ = result["camera_candidates"][0]["angles_deg"]
    assert ax == pytest.approx(math.degrees(0.15), abs=2.5) and ay == pytest.approx(math.degrees(-0.05), abs=2.5)
    # The fitted window, repeated by the template, passes the same CAD-side checks.
    WheelSpec(**{**spec.model_dump(), **result["spec_patch"]})


def test_grid_uses_window_surface_even_for_incoming_loft_spec(monkeypatch):
    spec = WheelSpec(spoke_count=GROUPS, spoke_crown_mm=4)
    grid = wf.Grid(spec, GROUPS)
    observed = {}

    def capture(points, pose):
        observed["points"] = points
        return np.full((len(points), 2), 2.5)

    monkeypatch.setattr(wf, "project", capture)
    grid.sample({}, np.ones((5, 5)), np.ones((5, 5), bool), 1)
    xyz = observed["points"]
    radii = np.hypot(xyz[:, 0], xyz[:, 1])
    np.testing.assert_allclose(xyz[:, 2], front_z(spec, radii, method="window"), atol=1e-10)
    assert np.max(np.abs(xyz[:, 2]-front_z(spec, radii))) > 1.9


def test_overlay_uses_target_phase_and_window_surface(tmp_path, monkeypatch):
    spec = WheelSpec(spoke_count=GROUPS, spoke_phase_deg=17, spoke_crown_mm=4)
    outline = swept_window()
    result = {"groups": GROUPS, "window_outlines_mm": [outline], "held_out_groups": [],
              "spec_patch": {"spoke_phase_deg": 0.0}, "pose": {}}
    captured = []

    def capture(points, pose):
        captured.append(points)
        return points[:, :2] * .05 + 50

    monkeypatch.setattr(wf, "project", capture)
    photo = tmp_path / "input.png"
    Image.new("RGB", (100, 100), "white").save(photo)
    wf.overlay(photo, {"windows": []}, spec, result, tmp_path / "overlay.jpg")
    np.testing.assert_allclose(captured[0][:, :2], outline, atol=1e-10)
    for points in captured:
        radii = np.hypot(points[:, 0], points[:, 1])
        np.testing.assert_allclose(points[:, 2], front_z(spec, radii, method="window"), atol=1e-10)


def test_fit_ellipse_round_trip():
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    a, b, angle = 300.0, 220.0, 25.0
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    pts = np.column_stack([500 + a * np.cos(t) * c - b * np.sin(t) * s, 400 + a * np.cos(t) * s + b * np.sin(t) * c])
    e = wf.fit_ellipse(pts)
    assert (e["cx"], e["cy"], e["a"], e["b"], e["angle_deg"]) == pytest.approx((500, 400, a, b, angle), abs=1e-6)


def test_sector_consensus_aligns_jitter_and_rejects_one_bad_group():
    spec = WheelSpec(spoke_count=GROUPS, rim_diameter_in=20)
    grid = wf.Grid(spec, GROUPS)
    base = np.ones((len(grid.rs), grid.per_group), dtype=float)
    base[12:-12, grid.per_group // 4:grid.per_group // 2] = 0
    sectors = np.stack([base.copy() for _ in range(GROUPS)], axis=1)
    sectors[:, 1] = np.roll(sectors[:, 1], 5, axis=1)
    sectors[:, 4] = 1 - sectors[:, 4]
    valid = np.ones_like(sectors, dtype=bool)
    template, _, _, info = wf.sector_consensus(
        grid, sectors.reshape(len(grid.rs), -1), valid.reshape(len(grid.rs), -1))
    assert info["method"] == "phase_aligned_medoid_median_v2"
    assert 4 in info["outlier_groups"] and 4 not in info["fit_groups"]
    assert abs(info["phase_offsets_deg"][1]) > 0
    assert np.mean((template > 0.5) == (base > 0.5)) > 0.99


def test_cell_boundary_keeps_concave_notch_instead_of_radial_envelope():
    region = np.zeros((7, 7), dtype=bool)
    region[1:6, 1:6] = True
    region[2:5, 4:6] = False
    loops = wf._cell_boundary_loops(region)
    outer = max(loops, key=lambda points: abs(wf._loop_area(points)))
    assert abs(wf._loop_area(outer)) == pytest.approx(region.sum())
    assert (2, 4) in outer and (5, 4) in outer
