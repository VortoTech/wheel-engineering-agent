import json
from pathlib import Path

import numpy as np
import pytest

from wheelcam.forged_blank import recipe_from_dict

PRESET = Path(__file__).resolve().parents[1] / "experiments/forged-blank/recipes/hf6-y-split.json"


def outline_recipe(**extra):
    """The hf6-y-split preset as an outline-family recipe (its group-0 windows), plus `extra`."""
    import wheelcam.forged_blank as fb
    preset = json.loads(PRESET.read_text())
    base = recipe_from_dict(preset)
    loops = [np.array(o) for o in fb.window_outlines(base, samples=96)]
    pitch = 360 / base.spokes
    group0 = [o for o in loops if -pitch / 2 <= np.degrees(np.arctan2(*o.mean(0)[::-1])) < pitch / 2]
    outlines = [[[float(np.hypot(x, y)), float(np.degrees(np.arctan2(y, x)))] for x, y in o] for o in group0]
    return {**preset, "family": "outline", "outlines": outlines, **extra}


def test_loft_of_point_rings_is_a_closed_mesh_of_the_right_volume():
    from wheelcam.mesh_build import loft
    square = np.array([(0, 0), (10, 0), (10, 10), (0, 10)], float)
    rings = [np.column_stack([square, np.full(4, z)]) for z in (0.0, 5.0, 20.0)]
    m = loft(rings)
    assert not m.is_empty() and m.genus() == 0
    assert m.volume() == pytest.approx(2000, rel=1e-6)
    assert loft([r[::-1] for r in rings]).volume() == pytest.approx(2000, rel=1e-6)   # either winding


def test_mesh_blank_matches_the_brep_blank():
    from wheelcam.forged_blank import blank
    from wheelcam.mass_properties import volume
    from wheelcam.mesh_build import blank_profile, revolve
    p = recipe_from_dict(outline_recipe(hub_crease_r=80, hub_crease_z=-40))
    assert revolve(blank_profile(p)).volume() == pytest.approx(volume(blank(p)), rel=.005)


def test_mesh_build_cuts_every_stage_and_exports(tmp_path):
    """Windows (flanked), grooves, pads, valleys and lugs all remove material; one closed solid."""
    from wheelcam.mesh_build import build, export_glb
    p = recipe_from_dict(outline_recipe(flank_w=12, flank_depth=14, face_chamfer=2, hub_crease_r=80, hub_crease_z=-40,
                                        spoke_pad_w=24, spoke_pad_depth=6, spoke_pad_r=[90, 220], spoke_pad_style="pocket",
                                        outline_groove_r=[140, 215], hub_valley_depth=12, hub_arm_w=30))
    body, report = build(p)
    assert report["status"] == "Error.NoError" and report["genus"] > p.spokes
    ops = {s["op"]: s["removed_mm3"] for s in report["stages"]}
    assert set(ops) == {"through_windows", "spoke_grooves_outline", "spoke_pads", "hub_valleys", "lug_holes_and_seats"}
    assert all(v > 0 for v in ops.values()), ops
    assert 5 < report["mass_kg_6061"] < 30
    export_glb(body, tmp_path / "wheel.glb")
    data = (tmp_path / "wheel.glb").read_bytes()
    assert data[:4] == b"glTF" and b'"NORMAL"' in data                        # the viewer needs normals


def test_mesh_checks_and_scoring_take_the_mesh_part():
    """verify() names its checks as the B-Rep verify does, and visual_check scores a manifold."""
    from wheelcam.mesh_build import build, verify
    from wheelcam.visual_check import mesh
    p = recipe_from_dict(outline_recipe())
    body, _ = build(p)
    et = p.hub_z - p.web_thick_hub + p.width / 2
    spec = {"diameter_in": 20, "width_in": 9, "pcd_mm": p.pcd, "bolts": p.bolts, "et_mm": round(et, 1)}
    checks = verify(body, p, spec)
    assert set(checks) == {"single_valid_solid", "outer_diameter", "overall_width", "bolt_pattern", "offset_et",
                           "rotational_symmetry"}
    assert all(c["pass"] for c in checks.values()), checks
    tri, ids = mesh(body)
    assert tri.shape[1:] == (3, 3) and len(tri) == len(ids) > 1000


def test_symmetry_check_uses_the_symmetry_spokes_and_lugs_share():
    """8 spoke groups and 5 lugs share no rotation: nothing to compare (LCX-01, GNX-01)."""
    from wheelcam.mesh_build import build, verify
    p = recipe_from_dict(outline_recipe(bolts=5, pcd=112, seat_d=28, center_bore_r=33.3, hub_r=40))
    body, _ = build(p)
    sym = verify(body, p, {})["rotational_symmetry"]
    assert sym["pass"] and sym["sector_volume_spread"] is None and "note" in sym


def test_ridge_pads_keep_the_spine_and_slope_to_the_spoke_edges():
    """Ridge pads: the face stays on the skeleton and drops toward the spoke edge, with no shelf."""
    import numpy as np
    from wheelcam.forged_blank import face_z, spoke_centrelines
    from wheelcam.mesh_build import _cylinder, build
    p = recipe_from_dict(outline_recipe(spoke_pad_w=16, spoke_pad_depth=8, spoke_pad_r=[90, 230]))
    body, report = build(p)
    ops = {s["op"]: s["removed_mm3"] for s in report["stages"]}
    assert report["status"] == "Error.NoError" and ops["spoke_ridges"] > 0 and "spoke_pads" not in ops
    line, hw = next((l, h) for l, h in spoke_centrelines(p) if np.hypot(*l.T).min() < 160 < np.hypot(*l.T).max())
    k = int(np.argmin(np.abs(np.hypot(*line.T) - 160)))
    q, h = line[k], hw[k]
    t = line[min(k + 1, len(line) - 1)] - line[max(k - 1, 0)]
    n = np.array([-t[1], t[0]]) / np.hypot(*t)
    top = lambda xy: (body ^ _cylinder(.4, -p.width - 5, 60, *xy, 16)).bounding_box()[5]
    spine, side = top(q), top(q + n * (min(8, .45 * h) + .6 * (h - min(8, .45 * h))))
    assert spine == pytest.approx(face_z(p, float(np.hypot(*q))), abs=.6)
    assert spine - side > 1.0, (spine, side)


def test_conical_seat_from_the_order_hole_form():
    """A "15X32X60" hole form: 15 mm hole, 32 mm seat narrowing at 60 deg (the real orders' seats)."""
    import math
    from wheelcam.forged_blank import hole_form, seat_cone_height
    from wheelcam.mesh_build import _cylinder, build
    assert hole_form("15X32X60") == {"bolt_d": 15.0, "seat_d": 32.0, "seat_cone_deg": 60.0}
    assert hole_form("15*32*60") and not hole_form("32X15X60") and not hole_form("锥孔")
    p = recipe_from_dict({**outline_recipe(), **hole_form("15X32X60")})
    h = seat_cone_height(p)
    assert h == pytest.approx(8.5 / math.tan(math.radians(30)))
    body, _ = build(p)
    a = math.radians(180 / p.spokes)
    x, y = p.pcd / 2 * math.cos(a), p.pcd / 2 * math.sin(a)
    seat_z = p.hub_z - p.seat_depth
    ring = lambda r, z: (body ^ (_cylinder(r + .3, z - .2, z + .2, x, y, 64) - _cylinder(r - .3, z - .3, z + .3, x, y, 64))).volume()
    assert ring(10.5, seat_z - h / 2) < 1e-3                     # open half way down the cone (r 11.75 there)
    assert ring(9.5, seat_z - h - .5) > 1.0                      # below it only the 15 mm hole
