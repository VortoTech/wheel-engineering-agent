"""Base-front consistency checks against the actual CAD profile, not image IoU."""
import math

import cadquery as cq
import numpy as np
import pytest
from pydantic import ValidationError

from wheelcam.models import WheelSpec
from wheelcam.photo_pose import front_z, project, rotation, unproject_front
from wheelcam.template import layout, window_blank_profile
from test_windows import window


@pytest.mark.parametrize("crown", [0, 4])
@pytest.mark.parametrize("curve", [0, 0.5, 1])
def test_window_front_matches_occt_bezier_samples(crown, curve):
    spec = WheelSpec(spoke_method="window", window_outlines_mm=[window()],
                     spoke_crown_mm=crown, face_curve=curve)
    controls = layout(spec)["window_blank"]["top_rz"]
    edge = cq.Edge.makeBezier([cq.Vector(r, 0, z) for r, z in controls])
    # Ask OCCT for points on the exact curve used to construct the revolved body.
    points = [edge.positionAt(float(t), mode="parameter") for t in np.linspace(0, 1, 19)]
    radii = np.array([p.x for p in points]).reshape(1, -1)
    expected = np.array([p.z for p in points]).reshape(1, -1)
    assert front_z(spec, radii).shape == radii.shape
    np.testing.assert_allclose(front_z(spec, radii), expected, atol=1e-9, rtol=0)


def test_window_front_clamps_endpoints_without_claiming_rim_or_hub_shape():
    spec = WheelSpec(spoke_method="window", window_outlines_mm=[window()], spoke_crown_mm=4)
    (r0, z0), _, (r1, z1) = layout(spec)["window_blank"]["top_rz"]
    assert front_z(spec, [-10, r0-1, r0, r1, r1+1, 1000]) == pytest.approx([z0, z0, z0, z1, z1, z1])
    assert float(front_z(spec, r0)) == pytest.approx(z0)


def test_window_target_can_be_evaluated_before_outlines_without_weakening_validation():
    loft = WheelSpec(spoke_crown_mm=4)
    cad = WheelSpec(**{**loft.model_dump(), "spoke_method": "window", "window_outlines_mm": [window()]})
    radii = np.linspace(90, 210, 10)
    np.testing.assert_allclose(front_z(loft, radii, method="window"), front_z(cad, radii), atol=1e-10)
    # Analysis-only controls are not a buildable/accepted window specification.
    assert window_blank_profile(loft) == {key: layout(cad)["window_blank"][key] for key in ("top_rz", "bottom_rz")}
    assert loft.spoke_method == "loft" and not loft.window_outlines_mm
    with pytest.raises(ValidationError, match="至少一个"):
        WheelSpec(**{**loft.model_dump(), "spoke_method": "window"})


def test_nonzero_crown_is_not_the_legacy_loft_proxy():
    spec = WheelSpec(spoke_crown_mm=4)
    controls = window_blank_profile(spec)["top_rz"]
    r0, r1 = controls[0][0], controls[-1][0]
    for t in (0, .25, .5, .75, 1):
        radius = r0 + (r1-r0)*t
        assert front_z(spec, radius, method="window")-front_z(spec, radius) == pytest.approx(2*4*t*(1-t))


def test_legacy_loft_section_edge_proxy_remains_unchanged():
    spec = WheelSpec(spoke_crown_mm=4, face_curve=.8)
    first, last = layout(spec)["sections"][0], layout(spec)["sections"][-1]
    radii = np.linspace(first["r"]-10, last["r"]+10, 15)
    t = np.clip((radii-first["r"])/(last["r"]-first["r"]), 0, 1)
    expected = first["front"]+(last["front"]-first["front"])*((1-spec.face_curve)*t+spec.face_curve*t*t)
    np.testing.assert_allclose(front_z(spec, radii), expected, atol=1e-10)


def test_base_surface_does_not_claim_local_relief_or_ridge_geometry():
    base = WheelSpec(spoke_method="window", window_outlines_mm=[window()])
    detail = WheelSpec(**{**base.model_dump(), "window_face_relief_mm": 2, "window_spoke_ridge_mm": 2})
    np.testing.assert_allclose(front_z(base, [100, 150, 200]), front_z(detail, [100, 150, 200]))


def test_window_ray_roundtrip_with_crown():
    spec = WheelSpec(spoke_method="window", window_outlines_mm=[window()], spoke_crown_mm=4)
    pose = dict(cx=350., cy=310., scale_px=265., distance_radii=6.,
                rotation=rotation(.14, -.1, .035).tolist(), radius_mm=246.1, reference_z_mm=107.95)
    xy = np.array([[90, 0], [120, 25], [-120, 25], [0, -170], [200, -20]])
    xyz = np.column_stack([xy, front_z(spec, np.linalg.norm(xy, axis=1))])
    np.testing.assert_allclose(unproject_front(project(xyz, pose), pose, spec), xyz, atol=.002, rtol=0)


def test_window_base_elevation_separates_inside_outside_real_solid():
    from wheelcam.geometry import build_wheel

    spec = WheelSpec(spoke_method="window", spoke_count=5, spoke_crown_mm=4,
                     window_outlines_mm=[window(center=36, h0=10, h1=20)],
                     window_edge_fillet_mm=0, junction_fillet_mm=0)
    shape = build_wheel(spec)[0].val()
    assert shape.isValid() and len(shape.Solids()) == 1
    # Probe away from all junctions/window cuts, on each solid spoke web.
    for angle in np.radians(np.arange(5)*72):
        for radius in (115, 150, 185):
            x, y = radius*math.cos(angle), radius*math.sin(angle)
            z = float(front_z(spec, radius))
            point = cq.Vertex.makeVertex(x, y, z)
            # Boundary distance is the precision assertion. Point classifiers
            # are unstable very close to this revolved face at some angles, so
            # use a larger offset only for the qualitative material-side check.
            assert min(point.distance(face) for face in shape.Faces()) < 1e-7
            assert shape.isInside((x, y, z-1))
            assert not shape.isInside((x, y, z+1))
