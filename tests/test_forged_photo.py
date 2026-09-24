"""Photo -> forged recipe: synthetic round trips through a known camera (no labelled photos exist)."""
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from wheelcam.app import create_app
from wheelcam.forged_blank import ForgedWheel, recipe_from_dict, window_outlines, z_top
from wheelcam.forged_photo import FaceMap, fit_recipe

RECIPES = Path(__file__).resolve().parents[1] / "experiments" / "forged-blank" / "recipes"
ENVELOPE = ("lip_r", "lip_face_r_in", "barrel_outer_r", "barrel_inner_r", "width", "hub_r", "hub_z", "ring_r",
            "ring_z", "concavity_exp", "web_thick_hub", "web_thick_ring", "center_bore_r", "pcd", "seat_d")
CENTRE, SHIFT = np.array([420.0, 310.0]), np.array([.7, .15])     # image px, px per mm of depth


def camera(p, tilt_deg=35, rot_deg=12, phase_deg=17):
    """Weak perspective with tilt, in-plane rotation, spoke phase and a depth shift (concave face)."""
    a = 266.0
    b = a * math.cos(math.radians(tilt_deg))
    phi, psi = math.radians(rot_deg), math.radians(phase_deg)
    rot = np.array([[math.cos(phi), -math.sin(phi)], [math.sin(phi), math.cos(phi)]])

    def project(x, y, z=None):
        r, t = math.hypot(x, y), math.atan2(y, x) + psi
        local = np.array([a * r / p.lip_r * math.cos(t), b * r / p.lip_r * math.sin(t)])
        return (CENTRE + rot @ local + (z_top(p, r) if z is None else z) * SHIFT).tolist()
    return project


def clicks(name):
    """Rim, hub and one group's windows of a known recipe, as a user would click them."""
    truth = recipe_from_dict(json.loads((RECIPES / f"{name}.json").read_text()))
    project = camera(truth)
    rim = [project(truth.lip_r * math.cos(t), truth.lip_r * math.sin(t), 0.0) for t in np.linspace(0, 2 * math.pi, 9, endpoint=False)]
    hub = (CENTRE + truth.hub_z * SHIFT).tolist()
    pitch = 2 * math.pi / truth.spokes
    every = window_outlines(truth, samples=96)
    group = [o for o in every if -.1 < math.atan2(np.mean(o, axis=0)[1], np.mean(o, axis=0)[0]) < pitch / 2 + .1]
    windows = [[project(x, y) for x, y in o[::3]] for o in group]
    base = {**asdict(ForgedWheel()), **{k: getattr(truth, k) for k in ENVELOPE}}
    photo = synthetic_photo(truth, project, every)
    return truth, base, rim, hub, windows, photo


def synthetic_photo(p, project, outlines, size=(840, 640)):
    """A product-photo stand-in: white ground, dark face disc, white windows, through the camera."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.polygon([tuple(project(p.lip_r * math.cos(t), p.lip_r * math.sin(t), 0.0)) for t in np.linspace(0, 2 * math.pi, 180)], fill=(70, 70, 75))
    for outline in outlines:
        draw.polygon([tuple(project(x, y)) for x, y in outline], fill=(245, 245, 245))
    return np.asarray(image, float) / 255


@pytest.fixture(scope="module")
def work():
    return clicks("work6-tapered")


@pytest.fixture(scope="module")
def hf6():
    return clicks("hf6-y-split")


def test_face_map_round_trips_points():
    p = recipe_from_dict({})
    project = camera(p)
    rim = [project(p.lip_r * math.cos(t), p.lip_r * math.sin(t), 0.0) for t in np.linspace(0, 2 * math.pi, 8, endpoint=False)]
    face = FaceMap(p, rim, (CENTRE + p.hub_z * SHIFT).tolist())
    r_true = np.array([100.0, 150.0, 200.0])
    pts = [project(r * math.cos(.4), r * math.sin(.4)) for r in r_true]
    r, _ = face.to_face(pts)
    assert np.allclose(r, r_true, atol=.5)                     # depth parallax is corrected


def test_single_spoke_round_trip(work):
    truth, base, rim, hub, windows, _ = work
    recipe, report = fit_recipe(base, rim, hub, windows, truth.spokes, truth.bolts)
    assert report["family"] == "single" and recipe["spokes"] == 6
    assert abs(recipe["window_r_out"] - truth.window_r_out) < 1.0
    assert abs(recipe["window_r_in"] - truth.window_r_in) < 1.0
    assert report["window_iou"] >= .95
    assert len(report["overlay_spokes_px"]) == 6


def test_y_split_round_trip(hf6):
    truth, base, rim, hub, windows, _ = hf6
    recipe, report = fit_recipe(base, rim, hub, windows, truth.spokes, truth.bolts)
    assert report["family"] == "y_split"
    # Clicks are every 3rd outline point, and refinement trades a little radius against arm width.
    assert abs(recipe["window_r_out"] - truth.window_r_out) < 2.0 and abs(recipe["window_r_in"] - truth.window_r_in) < 2.0
    assert report["window_iou"] >= .9 and report["window_iou"] > report["window_iou_before_refine"]


def test_wrong_group_count_is_visible_in_iou(hf6):
    """Negative control: the 5-group mistake of the old HF6-4 label must score clearly worse."""
    truth, base, rim, hub, windows, _ = hf6
    _, right = fit_recipe(base, rim, hub, windows, 6)
    _, wrong = fit_recipe(base, rim, hub, windows, 5)
    assert wrong["window_iou"] < right["window_iou"] - .1


def test_photo_fit_endpoint(tmp_path, work):
    truth, base, rim, hub, windows, _ = work
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        ok = client.post("/api/forged/photo-fit", json={"rim_points": rim, "hub_point": hub, "windows": windows,
                                                        "groups": 6, "base_recipe": base})
        assert ok.status_code == 200 and ok.json()["recipe"]["family"] == "single"
        assert recipe_from_dict(ok.json()["recipe"]).spokes == 6          # the result is a valid recipe
        too_few = client.post("/api/forged/photo-fit", json={"rim_points": rim[:4], "hub_point": hub, "windows": windows, "groups": 6})
        assert too_few.status_code == 422
        three = client.post("/api/forged/photo-fit", json={"rim_points": rim, "hub_point": hub, "windows": windows * 3,
                                                           "groups": 6, "base_recipe": base})
        assert three.status_code == 422 and "网状" in three.json()["detail"]


@pytest.mark.parametrize("fixture, family", [("work", "single"), ("hf6", "y_split")])
def test_auto_detects_groups_and_windows_on_synthetic_photo(request, fixture, family):
    from wheelcam.forged_photo import auto_group_count, auto_windows
    truth, base, rim, hub, _, photo = request.getfixturevalue(fixture)
    groups, evidence = auto_group_count(photo, base, rim, hub)
    assert groups == truth.spokes, evidence                 # not a divisor (3) nor a multiple (12)
    windows, notes = auto_windows(photo, base, rim, hub, groups)
    recipe, report = fit_recipe(base, rim, hub, windows, groups)
    assert report["family"] == family and report["window_iou"] >= .9


def test_group_rule_prefers_the_largest_consistent_multiple():
    from wheelcam.forged_photo import choose_group_count
    # Measured sector spreads on the real photos: the divisor 3 agrees best, 6 is close, 12 is not.
    hf6 = {3: .1359, 4: .2402, 5: .2968, 6: .1573, 7: .3035, 8: .30, 9: .2935, 10: .3042, 11: .3059, 12: .2563}
    work = {3: .1768, 4: .2827, 5: .2907, 6: .2006, 7: .2957, 8: .3005, 9: .3029, 10: .3071, 11: .3065, 12: .2991}
    assert choose_group_count(hf6) == 6 and choose_group_count(work) == 6
    # Glossy black 6-spoke (27216): every sector is noisy; the old 1.3x-ratio rule stepped on to 12.
    glossy = {3: .2652, 4: .3437, 5: .3589, 6: .2882, 7: .364, 8: .3716, 9: .3766, 10: .3773, 11: .3762, 12: .3718}
    assert choose_group_count(glossy) == 6
    five = {3: .2564, 4: .2577, 5: .1846, 6: .2779, 7: .2782, 8: .283, 9: .283, 10: .282, 11: .2871, 12: .2879}  # WORK 5-spoke, 6 lugs
    assert choose_group_count(five) == 5
    assert choose_group_count({3: .30, 4: .28, 5: .10, 6: .29, 10: .31, 12: .3}) == 5   # a true 5 is not pushed to 10


def test_trace_outlines_recovers_the_windows(hf6):
    """Outline tracing on the synthetic HF6 photo: the same windows, as an outline-family recipe."""
    from wheelcam.forged_photo import trace_outlines
    from wheelcam.forged_blank import window_outlines as outlines_of
    truth, base, rim, hub, _, photo = hf6
    recipe, report = trace_outlines(photo, base, rim, hub, truth.spokes)
    assert recipe["family"] == "outline" and report["windows_per_group"] == 2
    assert report["mirror_agreement"] > .9
    p = recipe_from_dict(recipe)

    def area(outlines):
        return sum(abs(.5 * np.sum(np.asarray(o)[:, 0] * np.roll(np.asarray(o)[:, 1], -1)
                                   - np.roll(np.asarray(o)[:, 0], -1) * np.asarray(o)[:, 1])) for o in outlines)
    traced, true = area(outlines_of(p)), area(window_outlines(truth, samples=160))
    assert abs(traced - true) / true < .08, (traced, true)       # through-window area within 8 %
    assert len(report["overlay_windows_px"]) == 2 * truth.spokes


def test_outline_recipe_validation():
    with pytest.raises(ValueError, match="outlines"):
        recipe_from_dict({"family": "outline"})
    square = [[150 + 30 * math.cos(t), 10 * math.sin(t)] for t in np.linspace(0, 2 * math.pi, 12, endpoint=False)]
    assert recipe_from_dict({"family": "outline", "outlines": [square]}).outlines[0][0] == (180.0, 0.0)
