import math
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wheelcam.app import create_app
from wheelcam.forged_blank import TEMPLATE_VERSION, recipe_from_dict

# Plain spokes, no facets / grooves / pockets: the real CAD path in seconds (edge break is CAM-only).
FAST = {"family": "single", "spokes": 5, "facet_deg": 0, "groove_offsets": [],
        "back_pocket_skin": 0, "lip_pockets": 0}


def test_recipe_validation():
    assert recipe_from_dict({"_photo": "x.jpg"}).spokes == 6   # render hints are dropped
    with pytest.raises(ValueError, match="未知字段"):
        recipe_from_dict({"spokez": 5})
    with pytest.raises(ValueError, match="轮辐结构"):
        recipe_from_dict({"family": "mesh"})
    with pytest.raises(ValueError, match="nodes"):
        recipe_from_dict({"family": "skeleton"})
    with pytest.raises(ValueError, match="3–12"):
        recipe_from_dict({"spokes": 2})


def test_build_subprocess_dispatches_forged_template(tmp_path):
    snapshot = {"template": TEMPLATE_VERSION, "forged": FAST, "model_id": "m1", "draft_revision": 3}
    (tmp_path / "recipe.json").write_text(json.dumps(snapshot))
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "wheelcam.build", str(tmp_path / "recipe.json"), str(tmp_path)],
                   check=True, cwd=root, env={"PYTHONPATH": str(root / "services")}, timeout=600)
    report = json.loads((tmp_path / "report.json").read_text())
    # Fields the web viewer requires on every report.
    for key in ("checks", "solid_count", "volume_mm3", "bbox_mm", "face_count", "template_version", "limitations", "artifacts"):
        assert key in report
    assert report["template_version"] == TEMPLATE_VERSION
    assert all(report["checks"].values()) and report["solid_count"] == 1
    assert {"wheel.step", "wheel.glb", "stock.step", "recipe.json"} <= set(report["artifacts"])
    assert report["manufacturing_status"] == "not_released" and report["model_id"] == "m1"
    assert 0 < report["forged"]["removal_ratio"] < 1
    # The edge break is a CAM operation, not modelled geometry.
    assert report["cam_operations"] == [{"op": "window_rim_edge_break", "size_mm": 1.5, "angle_deg": 45,
                                         "edges": "all through-window rims, front (face) side",
                                         "note": "Not modelled in CAD; chamfer the sharp rim edges in CAM."}]
    assert "window_rim_edge_break" not in [s["op"] for s in report["forged"]["stages"]]
    # Main-template coordinates: Z=0 at the rim-width mid-plane, so the part is centred in Z.
    import cadquery as cq
    bbox = cq.importers.importStep(str(tmp_path / "wheel.step")).val().BoundingBox()
    assert abs(bbox.zmin + bbox.zmax) < 1e-3
    assert abs(bbox.zlen - recipe_from_dict(FAST).width) < 1e-3


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        yield client


def test_forged_build_endpoint_queues_template_snapshot(client):
    project = client.post("/api/projects", json={"name": "锻坯"}).json()
    url = f'/api/projects/{project["id"]}/forged-builds'
    assert client.post(url, json={"expected_revision": 1, "recipe": {"spokez": 5}}).status_code == 422
    queued = client.post(url, json={"expected_revision": 1, "recipe": FAST})
    assert queued.status_code == 202 and queued.json()["template"] == TEMPLATE_VERSION
    job = next(j for j in client.get(f'/api/projects/{project["id"]}').json()["jobs"] if j["id"] == queued.json()["id"])
    snapshot = job["snapshot"]
    assert snapshot["template"] == snapshot["template_version"] == TEMPLATE_VERSION
    assert snapshot["forged"]["spokes"] == 5 and snapshot["forged"]["family"] == "single"
    assert "spec" in snapshot                      # the viewer still reads the draft spec
    assert client.post(url, json={"expected_revision": 1, "recipe": FAST}).status_code == 409   # one job at a time
    assert client.post("/api/projects/nope/forged-builds", json={"expected_revision": 1}).status_code in (404, 409)


def test_presets_endpoint_returns_defaults_and_valid_recipes(client):
    data = client.get("/api/forged/presets").json()
    assert data["defaults"]["family"] == "y_split" and data["defaults"]["spokes"] == 6
    ids = [p["id"] for p in data["presets"]]
    assert {"hf6-y-split", "tree6-branching", "work6-tapered"} <= set(ids)
    for preset in data["presets"]:
        assert recipe_from_dict(preset["recipe"]).spokes >= 3        # every preset is a full, valid recipe
        assert not any(key.startswith("_") for key in preset["recipe"])
    tree = next(p for p in data["presets"] if p["id"] == "tree6-branching")["recipe"]
    assert tree["family"] == "skeleton" and len(tree["skeleton"]["edges"]) == 7


def test_forged_preparation_checks_caliper_stock_and_et(tmp_path):
    import hashlib
    from dataclasses import replace as dc_replace
    from wheelcam.forged_blank import export_model, z_back
    p = recipe_from_dict(FAST)
    et = z_back(p, p.hub_r) + p.width / 2                   # mounting face vs rim mid-plane
    caliper = {"inner_radius_mm": 90, "outer_radius_mm": 180, "z_min_mm": -70, "z_max_mm": -3,
               "required_clearance_mm": 2, "source": {"kind": "manual"}}
    stock = {"outer_diameter_mm": 600, "height_mm": 260, "center_z_mm": 0, "cavity_diameter_mm": 0,
             "front_web_mm": 260, "required_allowance_mm": 1, "source": {"kind": "manual"}}
    report = export_model(FAST, tmp_path, {"preparation": {"caliper": caliper, "stock": stock,
                                                           "material": {"name": "6061", "density_kg_m3": 2700}}})
    assert report["derived"]["offset_et_mm"] == round(et, 1)
    prep = report["preparation"]
    assert prep["caliper"]["status"] == "clear", prep["caliper"]             # inboard of the mounting face
    assert prep["stock"]["status"] == "contained" and prep["weight"]["status"] == "estimated"
    assert "caliper-envelope.step" in report["artifacts"]
    # The supplier blank is checked but must not replace the template's own forging blank.
    own = hashlib.sha256((tmp_path / "stock.step").read_bytes()).hexdigest()
    assert own == report["artifacts"]["stock.step"]["sha256"]
    assert prep["stock"]["stock_volume_mm3"] > report["forged"]["stock_volume_mm3"]   # 600 mm cylinder > own blank

    # Negative control: the same envelope pushed forward into the spokes must interfere.
    forward = dict(caliper, z_min_mm=5, z_max_mm=60)
    bad = export_model(FAST, tmp_path / "bad", {"preparation": {"caliper": forward}})
    assert bad["preparation"]["caliper"]["status"] == "interference"


def test_window_pockets_and_stem_slots_cut_the_part():
    """Spokes run out to the lip over window pockets; stem slots are through holes."""
    from wheelcam.forged_blank import build, spoke_geometry
    plain = dict(FAST, ring_r=234, ring_z=-8, lip_face_r_in=258, window_r_out=229)
    styled = dict(plain, window_pocket_r=252, stem_slots=[[88, 118, 16, 8], [150, 160, 0, 8]])
    p = recipe_from_dict(styled)
    # The spoke footprint reaches past the pocket edge, so neighbouring pockets stay separate.
    assert max(max(abs(x) for x, _ in poly) for poly in spoke_geometry(p)[0]) >= 252
    _, part, stages = build(p)
    assert part.isValid() and len(part.Solids()) == 1
    removed = {s["op"]: s["removed_mm3"] for s in stages}
    # 3 slots per spoke x 5 spokes, each ~ (30 x 8 minus the round ends) x the spoke thickness.
    assert removed["stem_slots"] > 5 * 3 * 150 * 20
    assert removed["window_pockets"] > 0
    assert "window_pockets" not in {s["op"] for s in build(recipe_from_dict(plain))[2]}
    with pytest.raises(ValueError, match="stem_slots"):
        recipe_from_dict({"stem_slots": [[120, 90, 10, 8]]})


def test_flanked_windows_and_lug_pockets():
    """Flanked windows are cut one at a time (an overlapping compound blew OCC memory past 90 GB)."""
    from wheelcam.forged_blank import build, windows, window_outlines
    plain = recipe_from_dict(dict(FAST, spokes=4))
    styled = recipe_from_dict(dict(FAST, spokes=4, flank_w=6, flank_depth=8, lug_pocket_d=54))
    assert isinstance(windows(styled, window_outlines(styled)), list)
    _, a, stages_a = build(plain)
    _, b, stages_b = build(styled)
    assert b.isValid() and len(b.Solids()) == 1
    removed = lambda stages, op: next(s["removed_mm3"] for s in stages if s["op"] == op)
    # Flank section 1/2 x 6 x 8 = 24 mm2 along ~4 x 700 mm of window edge ~ 67 000 mm3.
    extra = removed(stages_b, "through_windows") - removed(stages_a, "through_windows")
    assert 40_000 < extra < 120_000, extra
    assert removed(stages_b, "lug_holes_and_seats") > removed(stages_a, "lug_holes_and_seats")


def test_face_surface_rounds_spoke_edges():
    """The face machining surface is cut last; spoke edges drop by the crown depth, spoke centres do not."""
    from wheelcam.forged_blank import build, face_profile, window_outlines
    p = recipe_from_dict(dict(FAST, face_crown_w=10, face_crown_depth=8, face_crown_q=2, face_grid_mm=3))
    stock, part, stages = build(p)
    assert part.isValid() and len(part.Solids()) == 1
    assert stages[-1]["op"] == "face_surface" and stages[-1]["removed_mm3"] > 0
    assert stock.BoundingBox().zmax > part.BoundingBox().zmax + 5          # the blank carries face stock
    # Probe the top at a spoke centre and 1.5 mm from its edge, at mid window radius.
    import cadquery as cq
    r = (p.window_r_in + p.window_r_out) / 2
    edge = min(window_outlines(p), key=lambda o: abs(math.hypot(*o[0]) - r))
    def top(x, y):
        hits = part.intersect(cq.Solid.makeBox(.4, .4, 400, cq.Vector(x - .2, y - .2, -300)))
        return hits.BoundingBox().zmax
    centre = top(r, 0.0)
    assert abs(centre - face_profile(p, r)) < 1.0, (centre, float(face_profile(p, r)))
    # Walk from the spoke centre line toward the first window at radius r until just inside material.
    angle = min(abs(math.atan2(y, x)) for x, y in edge if abs(math.hypot(x, y) - r) < 3)
    ex, ey = r * math.cos(angle - math.radians(1.5 * 180 / math.pi / r)), r * math.sin(angle - math.radians(1.5 * 180 / math.pi / r))
    assert top(ex, ey) < centre - 3, (top(ex, ey), centre)


def test_flank_offset_does_not_fold_at_a_sharp_inner_corner():
    """A traced window with a sharp inner corner: a 16 mm flank offset must not fold (invalid loft)."""
    import numpy as np
    from wheelcam.forged_blank import _crossing_segments, _flanked_window
    corners = np.array([(120, -30), (200, -30), (200, 30), (160, 30), (160, 0), (120, 0)], float)   # L shape
    loop = np.concatenate([a + (b - a) * t[:, None] for a, b in zip(corners, np.roll(corners, -1, axis=0))
                           for t in [np.arange(0, 1, 2.5 / np.linalg.norm(b - a))]])
    p = recipe_from_dict(dict(FAST, flank_w=16, flank_depth=18))
    tool = _flanked_window(p, pts=[tuple(v) for v in loop])
    assert tool.isValid()
    assert list(_crossing_segments(np.array([(0, 0), (10, 10), (10, 0), (0, 10)], float))) == [0, 2]   # bow tie


def test_window_envelope_never_cuts_through_the_lip_flange():
    """A deep window pocket with lowered spoke ends: the floor stays above the web back and the lip flange."""
    import numpy as np
    import cadquery as cq
    from wheelcam.forged_blank import POCKET_SKIN, _window_envelope, z_back
    p = recipe_from_dict(dict(FAST, ring_z=-20, window_pocket_depth=60, ring_r=234, lip_face_r_in=258))   # HF6-4 radii
    env = _window_envelope(p)
    def lowest(r):
        probe = cq.Solid.makeCylinder(.2, 400, cq.Vector(r * .7071, r * .7071, 100), cq.Vector(0, 0, -1))   # off the revolve seam
        return env.intersect(probe).BoundingBox().zmin
    assert lowest(p.ring_r + 2) == pytest.approx(z_back(p, p.ring_r) + POCKET_SKIN, abs=.01)
    lip_back = -min(14.0, p.lip_r - p.barrel_outer_r)
    for r in np.linspace(p.barrel_outer_r + .5, p.lip_face_r_in - 1.5, 6):       # the flange's back, from blank()
        back = np.interp(r, [p.barrel_outer_r, p.barrel_outer_r + 2, p.lip_r], [lip_back - 26, lip_back - 12, lip_back])
        assert lowest(r) >= back + POCKET_SKIN - .05, r


def test_hub_valleys_drop_the_hub_between_the_arms():
    """Valleys between the arms round each lug: valid cut, real volume, and a clear error when they cannot fit."""
    from wheelcam.forged_blank import blank, hub_valley_tools
    p = recipe_from_dict(dict(FAST, spokes=6, bolts=6, seat_d=27.5, hub_valley_depth=12, hub_arm_w=30))
    tools = hub_valley_tools(p)
    assert len(tools) == 1 and tools[0].isValid()
    body = blank(p)
    cut = body.cut(tools[0])
    assert cut.isValid() and len(cut.Solids()) == 1
    assert 3_000 < body.Volume() - cut.Volume() < 20_000                        # only below the hub face
    assert hub_valley_tools(recipe_from_dict(FAST)) == []                      # off by default
    with pytest.raises(ValueError, match="no floor"):
        hub_valley_tools(recipe_from_dict(dict(FAST, spokes=6, hub_valley_depth=12, hub_arm_w=70)))


def test_hub_valleys_move_out_until_the_floor_fits():
    """Wide arms leave no floor at bore + 6 (HF6-5, 2026-09-26): the auto inner radius moves out instead."""
    from wheelcam.forged_blank import _valley_radii, hub_valley_tools
    narrow = recipe_from_dict(dict(FAST, spokes=6, bolts=6, seat_d=27.5, hub_valley_depth=12, hub_arm_w=30))
    wide = recipe_from_dict(dict(FAST, spokes=6, bolts=6, seat_d=27.5, hub_valley_depth=12, hub_arm_w=50))
    assert _valley_radii(narrow)[0] == narrow.center_bore_r + 6
    assert _valley_radii(wide)[0] > wide.center_bore_r + 6
    assert hub_valley_tools(wide)[0].isValid()
    assert _valley_radii(recipe_from_dict(dict(FAST, spokes=6, hub_valley_depth=12, hub_arm_w=70))) is None


def test_small_windows_get_a_flank_in_proportion():
    """A 16 mm flank round a 15 mm triangle made a round blob; small windows get <= WINDOW_SHARE x their size."""
    import numpy as np
    import wheelcam.forged_blank as fb
    p = recipe_from_dict(dict(FAST, flank_w=16, flank_depth=18))
    # 12 mm: the cutter-radius rounding (TOOL_R) makes a 15 mm triangle big enough for a 4 mm flank
    tri = np.array([(150 + 12 * np.cos(a), 12 * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 3, endpoint=False)])
    loop = np.concatenate([a + (b - a) * t[:, None] for a, b in zip(tri, np.roll(tri, -1, axis=0))
                           for t in [np.linspace(0, 1, 40, endpoint=False)]])
    big = np.column_stack([150 + (loop[:, 0] - 150) * 6, loop[:, 1] * 6])
    seen = []
    orig = fb._unfold
    fb._unfold = lambda xy, n, reach, rounds=60: (seen.append(np.max(reach)), orig(xy, n, reach, rounds))[1]
    try:
        tools = fb.windows(p, [loop.tolist(), (big + [250, 0]).tolist()])
    finally:
        fb._unfold = orig
    assert all(t.isValid() for t in tools)
    assert fb.WINDOW_SHARE * fb._window_size(loop) < fb.MIN_FLANK                 # small: straight walls (CAM chamfer)
    assert len(seen) == 1 and seen[0] == pytest.approx(16, abs=.5)             # big: the full flank
    mid = recipe_from_dict(dict(FAST, flank_w=16, flank_depth=18))
    square = np.array([(150 + 10 * c, 10 * s_) for c, s_ in [(-1, -1), (1, -1), (1, 1), (-1, 1)]], float)
    ring = np.concatenate([a + (b - a) * t[:, None] for a, b in zip(square, np.roll(square, -1, axis=0))
                           for t in [np.linspace(0, 1, 30, endpoint=False)]])
    seen.clear()
    fb._unfold = lambda xy, n, reach, rounds=60: (seen.append(np.max(reach)), orig(xy, n, reach, rounds))[1]
    try:
        fb.windows(mid, [ring.tolist()])
    finally:
        fb._unfold = orig
    size = fb._window_size(fb._machinable(ring))                               # the loop as cut
    assert seen[0] <= fb.WINDOW_SHARE * size + 1e-6 < 16                        # mid-size: flank in proportion


def test_build_stops_when_a_cut_adds_material(monkeypatch):
    """A boolean that merges its tool (seen with thin flank lofts) must fail the build, not ship."""
    import cadquery as cq
    import wheelcam.forged_blank as fb
    post = cq.Solid.makeCylinder(5, 60, cq.Vector(150, 0, -20))
    monkeypatch.setattr(fb, "lug_tools", lambda p: [post])
    monkeypatch.setattr(cq.Shape, "cut", lambda self, *tools, **kw: self.fuse(*tools))
    with pytest.raises(RuntimeError, match="added material"):
        fb.build(recipe_from_dict(dict(FAST, facet_deg=0)))


def test_outline_grooves_follow_the_spoke_centrelines():
    """Traced (outline) spokes get a groove along each section's centreline inside the band."""
    import numpy as np
    import wheelcam.forged_blank as fb
    preset = json.loads((Path(__file__).resolve().parents[1] / "experiments/forged-blank/recipes/hf6-y-split.json").read_text())
    loops = [np.array(o) for o in fb.window_outlines(recipe_from_dict(preset), samples=96)]
    pitch = 360 / recipe_from_dict(preset).spokes
    group0 = [o for o in loops if -pitch / 2 <= np.degrees(np.arctan2(*o.mean(0)[::-1])) < 3 * pitch / 2]
    outlines = [[[float(np.hypot(x, y)), float(np.degrees(np.arctan2(y, x)))] for x, y in o] for o in group0]
    outline = {**preset, "family": "outline", "outlines": outlines}
    lines = fb.spoke_centrelines(recipe_from_dict(outline))
    assert lines and all(len(line) == len(w) for line, w in lines)
    tools = fb.outline_groove_tools(recipe_from_dict({**outline, "outline_groove_r": [140, 215]}))
    assert tools and all(t.isValid() for t in tools)
    assert fb.outline_groove_tools(recipe_from_dict(outline)) == []                 # off by default


def test_hub_crease_gives_a_sharp_platform_edge():
    """Straight chamfer to the crease, gentler dish beyond: the slope jumps there and the blank keeps the edge."""
    import cadquery as cq
    from wheelcam.forged_blank import blank, z_top
    p = recipe_from_dict(dict(FAST, hub_r=60, hub_z=-58, hub_crease_r=78, hub_crease_z=-47,
                              ring_z=-20, concavity_exp=1.0))
    assert z_top(p, 60) == -58 and z_top(p, 78) == pytest.approx(-47)
    inner = (z_top(p, 78) - z_top(p, 70)) / 8
    outer = (z_top(p, 86) - z_top(p, 78)) / 8
    assert inner > 2.5 * outer > 0
    b = blank(p)
    assert b.isValid()
    # the crease is an edge of the revolved front: a circle at r = 78 lying at z = -47
    circles = [e for e in b.Edges() if e.geomType() == "CIRCLE" and abs(e.radius() - 78) < .01]
    assert any(abs(e.Center().z + 47) < .01 for e in circles)
    assert z_top(recipe_from_dict(FAST), 100) == z_top(recipe_from_dict(dict(FAST, hub_crease_z=-10)), 100)   # off by default


def test_spoke_pads_leave_the_spoke_centre_proud():
    """Traced spokes keep a pad along their skeleton; the face beside it is lowered under the dish."""
    import math
    import numpy as np
    import cadquery as cq
    import wheelcam.forged_blank as fb
    preset = json.loads((Path(__file__).resolve().parents[1] / "experiments/forged-blank/recipes/hf6-y-split.json").read_text())
    base = recipe_from_dict(preset)
    loops = [np.array(o) for o in fb.window_outlines(base, samples=96)]
    pitch = 360 / base.spokes
    group0 = [o for o in loops if -pitch / 2 <= np.degrees(np.arctan2(*o.mean(0)[::-1])) < pitch / 2]
    outlines = [[[float(np.hypot(x, y)), float(np.degrees(np.arctan2(y, x)))] for x, y in o] for o in group0]
    p = recipe_from_dict({**preset, "family": "outline", "outlines": outlines, "spoke_pad_w": 24,
                          "spoke_pad_depth": 6, "spoke_pad_r": [base.window_r_in - 10, base.ring_r - 10]})
    tools = fb.spoke_pad_tools(p)
    assert tools and all(t.isValid() for t in tools)
    body = fb.blank(p)
    for t in tools:                                                                # group 0 (build rotates them)
        body = body.cut(t)
    assert body.isValid() and body.BoundingBox().zmax < .5
    line, hw = max(fb.spoke_centrelines(p), key=lambda lw: len(lw[0]))          # the longest spoke section
    x, y = line[len(line) // 2]

    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
    from OCP.gp import gp_Pnt
    state = lambda z: BRepClass3d_SolidClassifier(body.wrapped, gp_Pnt(float(x), float(y), z), 1e-6).State()
    z = fb.face_z(p, math.hypot(x, y))
    assert state(z - 1) == TopAbs_IN and state(z + 1) == TopAbs_OUT              # the pad keeps the dish height
    assert fb.spoke_pad_tools(recipe_from_dict(FAST)) == []                        # off / not traced


def test_face_chamfer_is_built_into_window_tools():
    """face_chamfer adds a crest chamfer to straight and flanked windows; cuts stay valid and only a
    chamfer ring more material goes."""
    import numpy as np
    import wheelcam.forged_blank as fb
    square = np.array([(150 + 12 * c, 12 * s_) for c, s_ in [(-1, -1), (1, -1), (1, 1), (-1, 1)]], float)
    loop = np.concatenate([a + (b - a) * t[:, None] for a, b in zip(square, np.roll(square, -1, axis=0))
                           for t in [np.linspace(0, 1, 30, endpoint=False)]])
    from wheelcam.forged_photo import _polygon_loop
    loop = _polygon_loop(loop, corner_r=4)
    closed = np.vstack([loop, loop[:1]])                                           # evenly resampled, as
    cum = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])   # the build does
    u = np.linspace(0, cum[-1], 160, endpoint=False)
    loop = np.column_stack([np.interp(u, cum, closed[:, 0]), np.interp(u, cum, closed[:, 1])])
    import wheelcam.mesh_build as mb
    removed = {}
    for c in (0.0, 2.0):
        p = recipe_from_dict(dict(FAST, face_chamfer=c))
        tool = fb._prism_window(p, loop.tolist())
        cut = fb.blank(p).cut(tool)
        assert tool.isValid() and cut.isValid() and len(cut.Solids()) == 1, c
        # Volumes on the mesh kernel the builds use: the OCC cut of the chamfer loft came out 690 mm3
        # smaller than the straight cut it contains (2026-09-27).
        b = mb.revolve(mb.blank_profile(p))
        removed[c] = b.volume() - (b - mb.loft(fb._prism_rings(p, loop, c))).volume()
    extra = removed[2.0] - removed[0.0]
    assert 50 < extra < 2_000, extra                     # ~ perimeter 90 mm x chamfer section 2-6 mm2
    p = recipe_from_dict(dict(FAST, face_chamfer=2.0, flank_w=8, flank_depth=10))
    assert fb._flanked_window(p, pts=loop.tolist()).isValid()       # flanked cuts are checked in the HF6-4 build


def test_sector_build_matches_the_whole_wheel_build():
    """Outline wheels are cut as one sector and patterned: same solid as cutting the whole wheel."""
    import numpy as np
    import wheelcam.forged_blank as fb
    from wheelcam.mass_properties import volume
    preset = json.loads((Path(__file__).resolve().parents[1] / "experiments/forged-blank/recipes/hf6-y-split.json").read_text())
    base = recipe_from_dict(preset)
    pitch = 360 / base.spokes
    loops = [np.array(o) for o in fb.window_outlines(base, samples=96)]
    group0 = [o for o in loops if -pitch / 2 <= np.degrees(np.arctan2(*o.mean(0)[::-1])) < pitch / 2]
    outlines = [[[float(np.hypot(x, y)), float(np.degrees(np.arctan2(y, x)))] for x, y in o] for o in group0]
    p = recipe_from_dict({**preset, "family": "outline", "outlines": outlines, "lip_pockets": 0,
                          "back_pocket_skin": 0, "groove_offsets": [], "facet_deg": 0})
    _, sector, stages = fb.build(p)
    assert any(s["op"] == "sector_pattern" for s in stages)
    _, whole, _ = fb.build(p, sector=False)
    assert sector.isValid() and len(sector.Solids()) == 1
    assert volume(sector) == pytest.approx(volume(whole), rel=1e-3)


def test_an_untrimmable_window_borrows_its_copy_from_another_group_not_its_mirror():
    """HF6-5: one window trimmed to nothing; the same window a pitch round is turned back into place."""
    import cadquery as cq
    import numpy as np
    from wheelcam.forged_blank import _borrow_trimmed
    tri = np.array([(150, 0), (200, 10), (160, 40)], float)            # asymmetric: its mirror is not a rotation
    mirror = tri * [1, -1]
    turn = lambda xy, deg: xy @ np.array([[np.cos(np.radians(deg)), np.sin(np.radians(deg))],
                                          [-np.sin(np.radians(deg)), np.cos(np.radians(deg))]])
    outlines = [tri, mirror, turn(tri, 120), turn(mirror, 120)]
    prism = lambda xy: cq.Workplane('XY').polyline([tuple(q) for q in xy]).close().extrude(10).val()
    trimmed = [cq.Compound.makeCompound([])] + [prism(xy) for xy in outlines[1:]]
    out = _borrow_trimmed(trimmed, [xy.tolist() for xy in outlines])
    bb, want = out[0].BoundingBox(), prism(tri).BoundingBox()
    assert abs(bb.xmin - want.xmin) < .01 and abs(bb.ymax - want.ymax) < .01 and abs(bb.ymin - want.ymin) < .01


def test_a_folding_flank_falls_back_to_a_beveled_straight_window(monkeypatch):
    """HF-3: a flank offset that folds whatever its reach no longer stops the build."""
    import numpy as np
    import wheelcam.forged_blank as fb
    p = recipe_from_dict(dict(FAST, flank_w=16, flank_depth=18, face_chamfer=2))
    loop = [(150 + 60 * np.cos(a), 60 * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 80, endpoint=False)]

    def folds(*args, **kwargs):
        raise fb.FoldError("flank offset still folds")
    monkeypatch.setattr(fb, "_flank_rings", folds)
    tools = fb.windows(p, [loop])
    assert len(tools) == 1 and tools[0].isValid()


def test_robust_cut_drops_free_faces_left_beside_the_solid(monkeypatch):
    """HF6-1: a stem-slot cut came back as a solid plus free faces, which the volume check rejects."""
    import cadquery as cq
    from wheelcam.forged_blank import _robust_cut
    from wheelcam.mass_properties import volume
    box = cq.Solid.makeBox(10, 10, 10)
    stray = cq.Face.makePlane(5, 5, cq.Vector(30, 0, 0))
    messy = cq.Compound.makeCompound([box, stray])
    monkeypatch.setattr(cq.Shape, "cut", lambda self, *tools, **kw: messy)
    out = _robust_cut(box, cq.Solid.makeBox(1, 1, 1, cq.Vector(20, 20, 20)), "test")
    assert len(out.Faces()) == 6 and volume(out) == pytest.approx(1000, rel=1e-6)


def test_window_tips_are_rounded_to_the_cutter_radius():
    """HF-3's slits ended in near-cusps whose flank offset crossed into fishtails; a cutter rounds them."""
    import numpy as np
    import wheelcam.forged_blank as fb
    t = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    slit = np.column_stack([150 + 40 * np.cos(t), 4 * np.sin(t) * np.abs(np.sin(t)) ** .2])   # pointed both ends
    out = fb._machinable(slit)
    assert len(out) == len(slit)
    tip = out[np.argmax(out[:, 0])]
    near = out[np.linalg.norm(out - tip, axis=1) < 2.5]
    assert np.ptp(near[:, 1]) > 3.0                        # a round end, not a point
    wide = out + fb._outward_normal(out) * 5.0
    assert len(fb._crossing_segments(wide)) == 0
    big = np.column_stack([150 + 40 * np.cos(t), 30 * np.sin(t)])            # nothing to round
    moved = fb._machinable(big)
    assert np.min(np.linalg.norm(moved[:, None] - big[None], axis=2), axis=1).max() < .8   # point spacing ~1.1 mm
