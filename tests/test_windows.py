import math

import pytest
from pydantic import ValidationError

from wheelcam.models import WheelSpec, default_sources, migrate_spec
from wheelcam.template import layout
from wheelcam.windows import MIN_WEB_MM, check, rotate, self_intersects, separation


def window(center=30.0, r0=100.0, r1=200.0, h0=8.0, h1=18.0, n=20):
    """Radial window centred between spokes, angular half-width growing outward."""
    step = [r0 + (r1 - r0) * i / (n - 1) for i in range(n)]
    half = [h0 + (h1 - h0) * (r - r0) / (r1 - r0) for r in step]
    point = lambda r, a: (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
    return [point(r, center + h) for r, h in zip(step, half)] + [point(r, center - h) for r, h in zip(step[::-1], half[::-1])]


def test_window_spec_lays_out_blank_and_checks():
    spec = WheelSpec(spoke_method="window", window_outlines_mm=[window()])
    blank = layout(spec)["window_blank"]
    assert blank["window_count"] == 1 and blank["min_web_mm"] >= MIN_WEB_MM
    assert blank["top_rz"][0][0] < blank["top_rz"][-1][0]
    assert all(t[1] > b[1] for t, b in zip(blank["top_rz"], blank["bottom_rz"]))
    assert layout(WheelSpec())["window_blank"] is None


def test_window_method_ignores_loft_only_fields():
    # Lip extension and pockets belong to other modes; the window method keeps the lip and drops pockets.
    spec = WheelSpec(spoke_method="window", window_outlines_mm=[window()], lip_extension_mm=30, pocket_depth_mm=12)
    lay = layout(spec)
    assert lay["pockets"] == [] and lay["front_lip"] and lay["paired_slot"] is None


@pytest.mark.parametrize("outlines, reason", [
    ([], "至少一个"),
    ([window(h0=29.5, h1=29.5)], "过窄"),                 # 59° wide: neighbouring copies leave < 4 mm
    ([window(r1=262)], "超出"),
    ([window(r0=40, r1=80)], "中心盘"),
    ([window(), window(center=32)], "重叠"),
    ([[(100, 0), (200, 0), (100, 50), (200, 50), (150, -20), (120, -20), (110, -10), (105, -5)]], "自相交"),
])
def test_invalid_windows_rejected(outlines, reason):
    with pytest.raises(ValidationError, match=reason):
        WheelSpec(spoke_method="window", window_outlines_mm=outlines)


def test_geometry_helpers():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert not self_intersects(square)
    assert self_intersects([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert separation(square, [(x + 15, y) for x, y in square]) == pytest.approx(5)
    assert separation(square, [(x + 5, y) for x, y in square]) == 0.0
    assert rotate([(1, 0)], 90)[0] == pytest.approx((0, 1))
    info = check([window()], 6, 82.5, 246.1)
    assert info["open_area_mm2"] > 0


def test_window_method_builds_one_solid_with_open_windows():
    from wheelcam.geometry import build_wheel, inspect_shape
    spec = WheelSpec(spoke_method="window", spoke_count=5, window_outlines_mm=[window(center=36, h0=10, h1=20)])
    wheel, info = build_wheel(spec)
    shape = wheel.val()
    assert all(inspect_shape(shape, spec)["checks"].values())
    # Proves the child-process join ran: a silent fallback to the plain join would leave 0 mm here.
    assert info["junction_fillet_applied_mm"] == spec.junction_fillet_mm
    assert 0 < info["window_edges_rounded"] <= info["window_edges_total"]
    solid, lay = shape.Solids()[0], layout(spec)
    f0, f1 = lay["window_blank"]["top_rz"][0], lay["window_blank"]["top_rz"][-1]
    r = 150.0
    z_front = f0[1] + (f1[1] - f0[1]) * (r - f0[0]) / (f1[0] - f0[0]) - 6   # just under the dished front
    for k in range(spec.spoke_count):
        window_angle, spoke_angle = math.radians(36 + 72 * k), math.radians(72 * k)
        assert not solid.isInside((r * math.cos(window_angle), r * math.sin(window_angle), z_front))
        assert solid.isInside((r * math.cos(spoke_angle), r * math.sin(spoke_angle), z_front))


def test_v9_drafts_migrate_with_window_fields_defaulted():
    old = WheelSpec().model_dump()
    for key in ("spoke_method", "window_outlines_mm", "window_edge_fillet_mm"):
        old.pop(key)
    spec, sources = migrate_spec(old, {k: v for k, v in default_sources().items() if k in old})
    assert spec["spoke_method"] == "loft" and spec["window_outlines_mm"] == []
    assert "模板升级" in sources["spoke_method"]["note"]
