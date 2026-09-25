"""Visual check: a CAD part scored against a photo drawn from known geometry."""
import math
from dataclasses import replace

import cadquery as cq
import numpy as np
from PIL import Image, ImageDraw

from wheelcam.forged_blank import ForgedWheel
from wheelcam.visual_check import compare_front

P = replace(ForgedWheel(), lip_r=200.0, hub_r=50.0, spokes=6)


def part(hole_r=30.0):
    pts = [(120 * math.cos(math.radians(60 * k)), 120 * math.sin(math.radians(60 * k))) for k in range(6)]
    return cq.Workplane("XY").circle(P.lip_r).extrude(30).faces(">Z").workplane().pushPoints(pts).hole(2 * hole_r).val()


def photo(rot_deg, hole_r=30.0, centre=(260, 250), scale=1.1):
    """White ground, dark disc, see-through holes; image y points down."""
    im = Image.new("RGB", (520, 500), "white")
    d = ImageDraw.Draw(im)
    cx, cy = centre
    d.ellipse([cx - P.lip_r * scale, cy - P.lip_r * scale, cx + P.lip_r * scale, cy + P.lip_r * scale], fill=(60, 55, 50))
    for k in range(6):
        t = math.radians(60 * k + rot_deg)
        x, y = cx + 120 * scale * math.cos(t), cy - 120 * scale * math.sin(t)
        d.ellipse([x - hole_r * scale, y - hole_r * scale, x + hole_r * scale, y + hole_r * scale], fill="white")
    return np.asarray(im, float) / 255


def test_front_scores_find_the_phase_and_rank_the_better_match():
    same, _ = compare_front(part(), photo(12), (260, 250), P.lip_r * 1.1, P, size=300)
    assert same["window_iou"] > .9 and same["edge_mm"] < 2.5, same
    assert abs(abs(same["phase_deg"]) - 12) < 1.5, same
    wrong, overlay = compare_front(part(22.0), photo(12), (260, 250), P.lip_r * 1.1, P, size=300)
    assert wrong["window_iou"] < same["window_iou"] - .2 and wrong["missing_open_mm2"] > 1000, wrong
    assert overlay.shape == (300, 300, 3)
