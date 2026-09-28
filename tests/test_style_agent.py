import json
import math

import numpy as np
import pytest
from PIL import Image

from test_mesh_build import outline_recipe


def synthetic_lip(n, size=800):
    """A grey disc with n dark slots round its lip band, as a front photo would show lip windows."""
    yy, xx = np.mgrid[:size, :size]
    c, R = size / 2, size * .45
    r, t = np.hypot(xx - c, yy - c), np.arctan2(yy - c, xx - c)
    img = np.where(r < R, .6, 1.0)
    slots = (r > .85 * R) & (r < .92 * R) & (np.cos(n * t) > .3)
    img[slots] = .1
    return img, (c, c), R


def test_lip_window_count_reads_the_period_as_a_multiple_of_the_groups():
    from wheelcam.style_agent import lip_window_count
    gray, centre, R = synthetic_lip(15)
    assert lip_window_count(gray, centre, R, 5) == 15
    gray, centre, R = synthetic_lip(20)
    assert lip_window_count(gray, centre, R, 5) == 20


def test_agent_applies_what_it_saw_and_found_through_the_whitelist(tmp_path, monkeypatch):
    import wheelcam.style_agent as sa
    photo = Image.fromarray((synthetic_lip(18)[0] * 255).astype(np.uint8)).convert("RGB")     # 6 groups x 3
    monkeypatch.setattr(sa, "wheel_crop", lambda path: (photo, np.asarray(photo, float) / 255, (400, 400), 360.0))
    monkeypatch.setattr(sa, "edge_score", lambda r, *a: {"edge_mm": {16.0: 4.0, 8.0: 2.5, 4.0: 2.1, 0.0: 2.8}.get(
        r.get("flank_w", 0.0), 3.0), "window_iou": .95})
    recipe = outline_recipe(flank_w=16.0, flank_depth=22.0)
    asked = []
    ask = lambda images, q, key: (asked.append(key) or (True, .9))
    edited, log = sa.run(recipe, "front.jpg", tmp_path, ask=ask)
    assert asked == ["small_pockets_in_outer_ring"]
    assert edited["flank_w"] == 4.0 and edited["lip_pockets"] == 18
    assert log["steps"][-1]["result"] == "已采用"
    saved = json.loads((tmp_path / "style_agent.json").read_text())
    assert saved["changed"]["flank_w"] == {"from": 16.0, "to": 4.0}
    assert (tmp_path / "after_render.png").exists() and (tmp_path / "wheel.glb").exists()


def test_agent_leaves_the_style_alone_when_nothing_is_better(tmp_path, monkeypatch):
    import wheelcam.style_agent as sa
    photo = Image.new("RGB", (800, 800), (150, 150, 150))
    monkeypatch.setattr(sa, "wheel_crop", lambda path: (photo, np.asarray(photo, float) / 255, (400, 400), 360.0))
    monkeypatch.setattr(sa, "edge_score", lambda r, *a: {"edge_mm": 2.0, "window_iou": .95})
    recipe = outline_recipe(flank_w=16.0, flank_depth=22.0)
    edited, log = sa.run(recipe, "front.jpg", tmp_path, ask=lambda *a: (False, .95))
    assert edited == recipe and log["changed"] == {}
