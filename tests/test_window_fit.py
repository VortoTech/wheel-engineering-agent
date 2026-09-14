"""Window fitting on a synthetic labelled photo rendered through a known camera."""
import math

import numpy as np
import pytest

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
    windows = []
    for g in range(GROUPS):
        p = np.array(rotate(outline, g * 360 / GROUPS))
        uv = project(np.column_stack([p, front_z(spec, np.hypot(*p.T))]), pose)
        windows.append({"points": uv.tolist()})
    t = np.linspace(0, 2 * np.pi, 90, endpoint=False)
    outer = pose["radius_mm"]
    ring = project(np.column_stack([outer * np.cos(t), outer * np.sin(t), np.full(len(t), pose["reference_z_mm"])]), pose)
    return {"image": {"name": "synthetic", "sha256": "0" * 64, "width": 1400, "height": 1300},
            "rim": {**wf.fit_ellipse(ring), "rms_px": 0.0, "points": ring[::10].tolist()}, "hub": None,
            "windows": windows, "ignore": [], "meta": {"groups": GROUPS, "usable": True}}


def test_fit_recovers_swept_window_and_camera():
    spec = WheelSpec(spoke_count=GROUPS, rim_diameter_in=20)
    outer = spec.rim_diameter_in * INCH / 2 + FLANGE_HEIGHT
    rim_z = spec.rim_width_in * INCH / 2 + layout(spec)["derived"]["flange_thickness_mm"]
    pose = {"cx": 700.0, "cy": 650.0, "scale_px": 560.0, "distance_radii": 5.0,
            "rotation": rotation(0.15, -0.05, 0.0).tolist(), "radius_mm": outer, "reference_z_mm": rim_z,
            "angles_deg": [math.degrees(0.15), math.degrees(-0.05), 0.0]}
    truth = swept_window()
    label = synthetic(spec, truth, pose)
    result = wf.fit(label, spec, *wf.label_masks(label))
    assert result["window_error"] is None
    assert len(result["window_outlines_mm"]) == 1
    assert result["held_out_iou_mean"] > 0.93
    fitted = result["window_outlines_mm"][0]
    assert abs(area(fitted)) == pytest.approx(abs(area(truth)), rel=0.08)
    # The chosen camera tilts the right way (the ellipse alone cannot tell the sign).
    ax, ay, _ = result["camera_candidates"][0]["angles_deg"]
    assert ax == pytest.approx(math.degrees(0.15), abs=2.5) and ay == pytest.approx(math.degrees(-0.05), abs=2.5)
    # The fitted window, repeated by the template, passes the same CAD-side checks.
    WheelSpec(**{**spec.model_dump(), **result["spec_patch"]})


def test_fit_ellipse_round_trip():
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    a, b, angle = 300.0, 220.0, 25.0
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    pts = np.column_stack([500 + a * np.cos(t) * c - b * np.sin(t) * s, 400 + a * np.cos(t) * s + b * np.sin(t) * c])
    e = wf.fit_ellipse(pts)
    assert (e["cx"], e["cy"], e["a"], e["b"], e["angle_deg"]) == pytest.approx((500, 400, a, b, angle), abs=1e-6)
