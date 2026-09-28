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


def test_rim_section_has_bead_seats_a_drop_well_and_the_lip_above_the_seat():
    """The rim section learnt from the factory CAD of the real orders (2026-09-27)."""
    import numpy as np
    from wheelcam.forged_blank import FLANGE_H, WELL_DEPTH, rim_points
    p = recipe_from_dict(outline_recipe())
    pts = np.array(rim_points(p))
    R = p.lip_r - FLANGE_H
    assert pts[:, 0].max() == pytest.approx(p.lip_r)
    assert pts[:, 0].min() < R - WELL_DEPTH                         # the well's inside
    assert np.isclose(pts[:, 0], R - WELL_DEPTH).sum() >= 2          # a flat well floor
    assert pts[:, 1].min() == pytest.approx(-p.width)


def test_lip_windows_sit_over_the_windows_and_do_not_break_through():
    from wheelcam.mesh_build import build
    r = outline_recipe()
    p = recipe_from_dict(r)
    plain, _ = build(r)
    n = 2 * len([o for o in p.outlines]) * p.spokes
    body, report = build({**r, "lip_pockets": n, "lip_pocket_r": [p.ring_r + 2, p.lip_face_r_in - 3]})
    stage = [s for s in report["stages"] if s["op"] == "lip_windows"][0]
    assert stage["tools"] == n and stage["removed_mm3"] > 0
    assert body.genus() == plain.genus()                               # blind: no new holes


def test_hub_recess_leaves_a_boss_round_every_lug():
    from wheelcam.mesh_build import build, verify
    r = outline_recipe()
    p = recipe_from_dict(r)
    plain, _ = build(r)
    body, report = build({**r, "hub_recess_depth": 10})
    stage = [s for s in report["stages"] if s["op"] == "hub_recess"][0]
    assert stage["removed_mm3"] > 0 and body.genus() == plain.genus()
    assert verify(body, p, {})["bolt_pattern"]["pass"]
    import manifold3d as m3, math
    a = math.radians(180 / p.spokes)                    # at a lug, just outside its seat: the boss stands
    probe = lambda x, y: m3.Manifold.cylinder(4, 1.5, 1.5, 16).translate([x, y, p.hub_z - 5])
    x, y = (p.pcd / 2 + p.seat_d / 2 + 1.5) * math.cos(a), (p.pcd / 2 + p.seat_d / 2 + 1.5) * math.sin(a)
    assert (body ^ probe(x, y)).volume() > 1
    b = a + math.pi / p.bolts                            # between two lugs: sunk
    x, y = (p.pcd / 2) * math.cos(b), (p.pcd / 2) * math.sin(b)
    assert (body ^ probe(x, y)).volume() < 1e-6
