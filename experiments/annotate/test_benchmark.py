"""Synthetic check of benchmark.py: a tilted 5-group wheel whose windows are exact rotations must
score ~1 on held-out sectors; distorting one odd-sector window must lower that sector only."""
import copy
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark import evaluate, PER_SECTOR  # noqa: E402

W, H, GROUPS = 1600, 1200, 5
RIM = {"cx": 790.0, "cy": 610.0, "a": 520.0, "b": 470.0, "angle_deg": 17.0, "rms_px": 0.0}


def to_image(rho, phi):
    t = math.radians(RIM["angle_deg"])
    u, v = rho * math.cos(phi) * RIM["a"], rho * math.sin(phi) * RIM["b"]
    return [RIM["cx"] + u * math.cos(t) - v * math.sin(t), RIM["cy"] + u * math.sin(t) + v * math.cos(t)]


def window(k, stretch=1.0):
    # A curved window in rectified polar space: rho 0.38–0.85, angular half-width grows outward.
    centre = 2 * math.pi * (k + 0.5) / GROUPS
    left = [to_image(r, centre - (0.10 + 0.25 * (r - 0.38)) * stretch) for r in [0.38 + 0.47 * i / 20 for i in range(21)]]
    right = [to_image(r, centre + (0.10 + 0.25 * (r - 0.38)) * stretch) for r in [0.85 - 0.47 * i / 20 for i in range(21)]]
    return {"points": left + right}


def label(windows):
    rim = {**RIM, "points": [to_image(1.0, 2 * math.pi * i / 8) for i in range(8)]}
    return {"image": {"name": "synthetic", "sha256": "0" * 64, "width": W, "height": H}, "rim": rim, "hub": None,
            "windows": windows, "ignore": [], "meta": {"groups": GROUPS, "usable": True}}


def test_symmetric_wheel_scores_near_one():
    result = evaluate(label([window(k) for k in range(GROUPS)]))
    assert result["heldout_iou_min"] > 0.97, result


def test_distorted_window_lowers_only_its_sector():
    windows = [window(k) for k in range(GROUPS)]
    windows[3] = window(3, stretch=1.6)            # sector 3 is odd → held out
    result = evaluate(label(windows))
    iou = result["per_sector_iou"]
    assert iou[3] < 0.9, iou
    assert min(iou[k] for k in (0, 1, 2, 4)) > 0.97, iou


if __name__ == "__main__":
    test_symmetric_wheel_scores_near_one(); test_distorted_window_lowers_only_its_sector()
    print("benchmark synthetic tests passed; per-sector columns", PER_SECTOR)
